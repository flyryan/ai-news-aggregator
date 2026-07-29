"""In-memory capture of LLM streaming activity for the replay artifact.

This is an *observer* bolted onto the existing SSE path in ``llm_client``. It
records when each request queued, started, produced its first token and
finished, plus the model's own output deltas, so ``generators/replay_generator``
can rebuild the run as a timeline. See ``docs/replay-schema.md`` -- that document
is the contract; this module only produces the raw material for it.

Four rules drive every design choice here, in priority order over completeness:

1. **The pipeline must never fail because of replay.** Every public method is
   wrapped by :func:`_guarded`; the first exception disables the recorder for
   the rest of the run and logs exactly one warning.
2. **Memory only on the hot path.** ``record_delta`` runs inside the SSE loop
   for every token, so there is no I/O, no lock, and no ``await`` anywhere in
   this module. The buffers are read once, at the end of the run.
3. **Bounded.** Per-call and global delta caps trade completeness for a
   predictable memory ceiling; overflow flips a ``truncated`` flag instead of
   growing. The same applies to prompts, which are far larger than the output:
   see ``DEFAULT_MAX_PROMPT_CHARS``.
4. **No credentials, ever.** Prompts *are* captured and published -- this
   project is a showcase and its prompts, analyzers and workflows are all public
   already, so the instructions the models were given are part of what the
   artifact exists to show. What must never appear is a credential or a private
   endpoint host; ``generators/replay_generator._assert_publishable`` is the gate
   that enforces that, and it now runs over the prompt artifact too.

   Prompts are captured off the hot path: they arrive once per call in
   ``start_call``'s context, never inside the SSE loop.

Thread-safety is deliberately absent: every caller lives on the single asyncio
event loop, and adding a lock would violate rule 2 for no real gain.
"""

from __future__ import annotations

import functools
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Delta kinds, matching the stream artifact's `kind` array. Ints rather than
# strings because this array is one entry per coalesced chunk and gzips better.
DELTA_THINKING = 0
DELTA_TEXT = 1

# Defaults per the capture-layer contract. The per-call cap bounds one runaway
# generation; the global cap bounds a runaway *run*. Neither is the generator's
# LLM_REPLAY_MAX_BYTES, which caps the gzipped artifact after shaping.
DEFAULT_COALESCE_MS = 80
DEFAULT_MAX_DELTAS_PER_CALL = 20_000
DEFAULT_MAX_TOTAL_DELTAS = 400_000

# Prompt capture caps. Prompts dwarf the output: a research analyzer batch sends
# ~130k chars of item JSON to produce ~13k of summary, so a run's prompts are an
# order of magnitude larger than its deltas. These bound the run's memory and,
# downstream, the published artifact. Truncation is marked, never silent.
DEFAULT_MAX_PROMPT_CHARS = 400_000
DEFAULT_MAX_TOTAL_PROMPT_CHARS = 8_000_000

# Outcome vocabulary from the schema. `retried` is intentionally absent: the
# router retries by issuing a brand-new request on another route, so from here
# each attempt is an independent call and only the generator can stitch them.
OUTCOME_OK = "ok"
OUTCOME_TRUNCATED = "truncated"
OUTCOME_REFUSED = "refused"
OUTCOME_FAILED = "failed"


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment setting (mirrors the llm_client idiom).

    Duplicated rather than imported because ``llm_client`` imports *this*
    module; sharing the helper the other way round would be a cycle.
    """
    raw_value = os.environ.get(name)
    if raw_value is None or raw_value == "":
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    logger.warning(f"Ignoring invalid {name}={raw_value!r}; using {default}")
    return default


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    """Read an integer environment setting with validation."""
    raw_value = os.environ.get(name)
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning(f"Ignoring invalid {name}={raw_value!r}; using {default}")
        return default
    if value < minimum:
        logger.warning(f"Ignoring {name}={value}; minimum is {minimum}, using {default}")
        return default
    return value


def _guarded(method: Callable) -> Callable:
    """Make a recorder method incapable of breaking its caller.

    Returns ``None`` when the recorder is off or already disabled, and converts
    any exception into a permanent self-disable plus one warning. ``Exception``
    and not ``BaseException``: nothing here awaits, so ``CancelledError`` cannot
    originate inside these methods, and swallowing ``KeyboardInterrupt`` would
    fight the operator.
    """

    @functools.wraps(method)
    def wrapper(self: "ReplayRecorder", *args, **kwargs):
        if not self._enabled:
            return None
        try:
            return method(self, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- deliberate catch-all, see docstring
            self._disable(exc)
            return None

    return wrapper


class CallRecord:
    """Mutable per-call buffer. A plain class, not a dataclass, because it is
    allocated per LLM request and mutated per token; attribute writes are the
    whole hot path."""

    __slots__ = (
        "id",
        "request_id",
        "context",
        "caller",
        "provider_id",
        "provider_model",
        "analysis_profile",
        "adaptive_effort",
        "queued_ms",
        "start_ms",
        "first_token_ms",
        "end_ms",
        "wait_ms",
        "t",
        "kind",
        "text",
        "thinking_chars",
        "text_chars",
        "delta_events",
        "truncated",
        "dropped_deltas",
        "stop_reason",
        "outcome",
        "error_type",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "prompt_system",
        "prompt_messages",
        "prompt_truncated",
        "prompt_chars",
    )

    def __init__(self, call_id: str, request_id: Optional[int], context: Dict[str, Any], queued_ms: int):
        self.id = call_id
        self.request_id = request_id
        # Shallow copy of the whole context: metadata and char counts, and the
        # generator needs fields beyond the few promoted below -- attempt,
        # fallback_from, retry_reason, thinking_type.
        #
        # The prompt text is *popped* out into its own slots rather than left in
        # here. `context` is what feeds the index's per-call metadata, and the
        # prompts are megabytes that belong in the separate, lazily-fetched
        # prompt artifact -- leaving them in would inflate the index that every
        # page view loads.
        self.context: Dict[str, Any] = dict(context or {})
        self.prompt_system: Optional[str] = self.context.pop("system_text", None)
        self.prompt_messages: Optional[str] = self.context.pop("messages_text", None)
        self.prompt_truncated: bool = False
        self.prompt_chars: int = len(self.prompt_system or "") + len(self.prompt_messages or "")
        self.caller: str = self.context.get("caller") or "unknown"
        self.provider_id: Optional[str] = self.context.get("provider_id")
        self.provider_model: Optional[str] = self.context.get("provider_model")
        self.analysis_profile: Optional[str] = self.context.get("analysis_profile")
        self.adaptive_effort: Optional[str] = self.context.get("adaptive_effort")

        self.queued_ms = queued_ms
        self.start_ms: Optional[int] = None
        self.first_token_ms: Optional[int] = None
        self.end_ms: Optional[int] = None
        self.wait_ms: Optional[int] = None

        # Three parallel arrays rather than an array of objects: same shape as
        # the stream artifact, and materially smaller gzipped.
        self.t: List[int] = []
        self.kind: List[int] = []
        self.text: List[str] = []

        # Counters are incremented even when a delta is dropped by the cap, so
        # the index's char/event totals stay truthful about what the model
        # actually produced.
        self.thinking_chars = 0
        self.text_chars = 0
        self.delta_events = 0
        self.truncated = False
        self.dropped_deltas = 0

        self.stop_reason: Optional[str] = None
        self.outcome: Optional[str] = None
        self.error_type: Optional[str] = None
        self.input_tokens: Optional[int] = None
        self.output_tokens: Optional[int] = None
        self.cache_read_tokens: Optional[int] = None
        self.cache_creation_tokens: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Raw, unshaped view. The generator owns schema shaping."""
        return {
            "id": self.id,
            "request_id": self.request_id,
            "caller": self.caller,
            "provider_id": self.provider_id,
            "provider_model": self.provider_model,
            "analysis_profile": self.analysis_profile,
            "adaptive_effort": self.adaptive_effort,
            "context": self.context,
            "queued_ms": self.queued_ms,
            "start_ms": self.start_ms if self.start_ms is not None else self.queued_ms,
            "first_token_ms": self.first_token_ms,
            "end_ms": self.end_ms,
            "wait_ms": self.wait_ms,
            "thinking_chars": self.thinking_chars,
            "text_chars": self.text_chars,
            "delta_events": self.delta_events,
            "stop_reason": self.stop_reason,
            "outcome": self.outcome,
            "error_type": self.error_type,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "truncated": self.truncated,
            "dropped_deltas": self.dropped_deltas,
            "deltas": {"t": self.t, "kind": self.kind, "text": self.text},
            # `prompt_chars` is the size actually sent, even when the text below
            # was capped or dropped -- so the UI can always report the real size.
            "prompt_chars": self.prompt_chars,
            "prompt_truncated": self.prompt_truncated,
            "prompt": (
                {"system": self.prompt_system, "messages": self.prompt_messages}
                if (self.prompt_system or self.prompt_messages)
                else None
            ),
        }


class ReplayRecorder:
    """Process-global observer of LLM streaming activity.

    Usage mirrors :class:`agents.cost_tracker.CostTracker`::

        recorder = get_recorder()
        recorder.begin_run("2026-07-28")
        call_id = recorder.start_call(request_id, request_context)
        recorder.mark_started(call_id)
        recorder.record_delta(call_id, DELTA_TEXT, "hello")
        recorder.finish_call(call_id, response=response)
        data = recorder.snapshot()
    """

    def __init__(self, clock: Callable[[], float] = time.time):
        # Injectable clock so tests can drive coalescing and monotonic clamping
        # deterministically; production always uses time.time.
        self._clock = clock
        self.capture_enabled = _env_bool("LLM_REPLAY_CAPTURE", True)
        self.coalesce_ms = _env_int("LLM_REPLAY_COALESCE_MS", DEFAULT_COALESCE_MS)
        self.max_deltas_per_call = _env_int(
            "LLM_REPLAY_MAX_DELTAS", DEFAULT_MAX_DELTAS_PER_CALL, minimum=1
        )
        self.max_total_deltas = _env_int(
            "LLM_REPLAY_MAX_TOTAL_DELTAS", DEFAULT_MAX_TOTAL_DELTAS, minimum=1
        )
        # Prompt capture is separately switchable: it is the largest thing the
        # recorder holds, so an operator debugging memory can drop it without
        # losing the timeline.
        self.capture_prompts = _env_bool("LLM_REPLAY_CAPTURE_PROMPTS", True)
        self.max_prompt_chars = _env_int(
            "LLM_REPLAY_MAX_PROMPT_CHARS", DEFAULT_MAX_PROMPT_CHARS, minimum=1
        )
        self.max_total_prompt_chars = _env_int(
            "LLM_REPLAY_MAX_TOTAL_PROMPT_CHARS", DEFAULT_MAX_TOTAL_PROMPT_CHARS, minimum=1
        )

        self._enabled = self.capture_enabled
        self.disabled_reason: Optional[str] = None

        self.date: Optional[str] = None
        self._t0: Optional[float] = None
        self._t0_iso: Optional[str] = None
        self._last_ms = 0
        self._call_sequence = 0
        self._total_deltas = 0
        self._total_prompt_chars = 0
        self.truncated = False
        self._calls: Dict[str, CallRecord] = {}

    # -- internals -------------------------------------------------------

    def _disable(self, exc: Exception) -> None:
        """Stop recording permanently, complaining exactly once."""
        if not self._enabled:
            return
        self._enabled = False
        self.disabled_reason = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "Replay capture disabled for the rest of this run after an internal "
            f"error ({self.disabled_reason}). The pipeline is unaffected; the "
            "replay artifact will be partial or absent."
        )

    def _set_t0(self, now: float) -> None:
        self._t0 = now
        self._t0_iso = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
        self._last_ms = 0

    def _ms(self) -> int:
        """Milliseconds since run start, clamped monotonic.

        The floor is global rather than per-call. Appends happen in event-loop
        order, so a global maximum still yields monotonic per-call arrays, and
        it additionally survives a wall-clock regression (NTP step) consistently
        across every buffer. The side effect is intended: right after a
        regression a freshly queued call can inherit a later call's timestamp
        rather than travelling backwards.
        """
        if self._t0 is None:
            self._set_t0(self._clock())
        # round(), not int(): epoch seconds are ~1.7e9, where float64 spacing is
        # large enough that a difference meant to be exactly 0.200s comes back as
        # 0.19999999995 and truncates to 199ms. Rounding keeps offsets faithful.
        value = round((self._clock() - self._t0) * 1000)
        if value < self._last_ms:
            value = self._last_ms
        self._last_ms = value
        return value

    # -- public API ------------------------------------------------------

    @_guarded
    def begin_run(self, date: str) -> None:
        """Set the timebase origin and clear every buffer."""
        self.date = date
        self._set_t0(self._clock())
        self._call_sequence = 0
        self._total_deltas = 0
        self.truncated = False
        self._calls = {}
        logger.info(
            f"Replay capture armed for {date} "
            f"(coalesce={self.coalesce_ms}ms, cap={self.max_deltas_per_call}/call)"
        )

    @_guarded
    def start_call(self, request_id: Optional[int], context: Optional[Dict[str, Any]]) -> Optional[str]:
        """Open a span at *queue* time and return its stable id, e.g. ``c017``.

        The id is a process-global counter, not the caller's ``request_id``:
        that one is only unique per client instance, and a routed run has one
        client per provider, so ids would collide across routes.

        Callers must treat ``None`` as "capture is off" and skip the rest.
        """
        if self._t0 is None:
            # Defensive: a caller that never invoked begin_run still gets a
            # usable timebase anchored at its first request.
            self._set_t0(self._clock())
        self._call_sequence += 1
        call_id = f"c{self._call_sequence:03d}"
        record = CallRecord(call_id, request_id, context or {}, self._ms())
        self._apply_prompt_caps(record)
        self._calls[call_id] = record
        return call_id

    def _apply_prompt_caps(self, record: CallRecord) -> None:
        """Bound one call's captured prompt, and the run's total.

        Runs once per call at queue time, never in the SSE loop. Truncation keeps
        the *head* of each part: the instructions lead, and the tail of a giant
        prompt is the Nth item of a JSON list -- the least informative slice. It is
        always marked, so the UI can say "showing the first N of M chars" rather
        than presenting a cut prompt as the whole thing.
        """
        if not self.capture_prompts:
            record.prompt_system = None
            record.prompt_messages = None
            return

        remaining_run = self.max_total_prompt_chars - self._total_prompt_chars
        if remaining_run <= 0:
            # The run has spent its whole prompt budget; keep the measured size so
            # the UI can still report what was sent, but hold no text.
            record.prompt_system = None
            record.prompt_messages = None
            record.prompt_truncated = record.prompt_chars > 0
            return

        budget = min(self.max_prompt_chars, remaining_run)
        kept = 0
        for attr in ("prompt_system", "prompt_messages"):
            text = getattr(record, attr)
            if not text:
                continue
            room = budget - kept
            if room <= 0:
                setattr(record, attr, None)
                record.prompt_truncated = True
                continue
            if len(text) > room:
                setattr(record, attr, text[:room])
                record.prompt_truncated = True
                kept += room
            else:
                kept += len(text)
        self._total_prompt_chars += kept

    @_guarded
    def mark_started(self, call_id: Optional[str]) -> None:
        """Note that the request cleared the concurrency semaphore and went out.

        Split from :meth:`start_call` so ``wait_ms`` is a measured queue delay
        rather than an inference, on both the instrumented and the
        logging-disabled paths.
        """
        record = self._calls.get(call_id) if call_id else None
        if record is None:
            return
        record.start_ms = self._ms()
        record.wait_ms = record.start_ms - record.queued_ms

    @_guarded
    def record_delta(self, call_id: Optional[str], kind: int, text: str) -> None:
        """Append one model output delta. Hot path -- keep it cheap.

        Only ``thinking_delta`` and ``text_delta`` reach here; ``signature_delta``
        is a cryptographic blob with nothing to render and is never recorded.

        Coalescing merges a delta into the previous entry when the kind matches
        and the previous entry *started* less than ``coalesce_ms`` ago. Anchoring
        the window to the entry's own start (rather than to the last append)
        bounds each entry to one window of wall time; anchoring to the last
        append would let a continuous stream collapse into a single giant entry
        and destroy the typewriter effect the artifact exists for. The
        comparison is strict so ``LLM_REPLAY_COALESCE_MS=0`` genuinely disables
        merging rather than merging everything sharing a millisecond.
        """
        record = self._calls.get(call_id) if call_id else None
        if record is None or not text:
            return

        record.delta_events += 1
        if kind == DELTA_THINKING:
            record.thinking_chars += len(text)
        else:
            record.text_chars += len(text)

        now_ms = self._ms()
        if record.t and record.kind[-1] == kind and (now_ms - record.t[-1]) < self.coalesce_ms:
            record.text[-1] += text
            return

        # Caps drop the tail, never the head: first_token_ms stays valid and a
        # truncated call still shows how it opened.
        if len(record.t) >= self.max_deltas_per_call or self._total_deltas >= self.max_total_deltas:
            record.truncated = True
            record.dropped_deltas += 1
            self.truncated = True
            return

        if record.first_token_ms is None:
            record.first_token_ms = now_ms
        record.t.append(now_ms)
        record.kind.append(kind)
        record.text.append(text)
        self._total_deltas += 1

    @_guarded
    def finish_call(
        self,
        call_id: Optional[str],
        response: Optional[Any] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        """Close a span with its terminal state, token usage and outcome."""
        record = self._calls.get(call_id) if call_id else None
        if record is None:
            return
        if record.outcome is not None:
            # First terminal state wins. A late second call (e.g. a teardown
            # failure after the response already landed) must not relabel a
            # successful call as failed.
            return

        record.end_ms = self._ms()
        if record.start_ms is None:
            # The request never cleared the semaphore -- it spent its whole life
            # queued. Reporting wait_ms=0 here would invert the truth on the
            # Gantt, so credit the full span to the queue. `error_type` (usually
            # CancelledError) is what tells the generator these never went out.
            record.start_ms = record.end_ms
            record.wait_ms = record.end_ms - record.queued_ms

        if error is not None:
            record.outcome = OUTCOME_FAILED
            record.error_type = type(error).__name__
            return

        stop_reason = getattr(response, "stop_reason", None)
        record.stop_reason = stop_reason
        if stop_reason == "refusal":
            record.outcome = OUTCOME_REFUSED
        elif stop_reason == "max_tokens":
            record.outcome = OUTCOME_TRUNCATED
        else:
            record.outcome = OUTCOME_OK

        usage = getattr(response, "usage", None)
        if usage is not None:
            record.input_tokens = getattr(usage, "input_tokens", None)
            record.output_tokens = getattr(usage, "output_tokens", None)
            record.cache_read_tokens = getattr(usage, "cache_read_input_tokens", None)
            record.cache_creation_tokens = getattr(usage, "cache_creation_input_tokens", None)

    def snapshot(self) -> Dict[str, Any]:
        """Everything captured, raw and unshaped, for the generator.

        Deliberately not ``@_guarded``: a disabled recorder still holds whatever
        it captured before it died, and a partial replay beats none. Callers get
        a dict in every case, with ``enabled``/``disabled_reason`` describing how
        much to trust it.
        """
        try:
            duration_ms = self._last_ms if self._t0 is not None else 0
            return {
                "enabled": self._enabled,
                "capture_enabled": self.capture_enabled,
                "disabled_reason": self.disabled_reason,
                "date": self.date,
                "t0_epoch": self._t0,
                "t0": self._t0_iso,
                "duration_ms": duration_ms,
                "coalesce_ms": self.coalesce_ms,
                "max_deltas_per_call": self.max_deltas_per_call,
                "max_total_deltas": self.max_total_deltas,
                "total_deltas": self._total_deltas,
                "truncated": self.truncated,
                "calls": [record.to_dict() for record in self._calls.values()],
            }
        except Exception as exc:  # noqa: BLE001 -- snapshot must not break the run either
            self._disable(exc)
            return {
                "enabled": False,
                "capture_enabled": self.capture_enabled,
                "disabled_reason": self.disabled_reason,
                "date": self.date,
                "calls": [],
            }


# Global recorder instance, mirroring the cost tracker's singleton idiom.
_global_recorder: Optional[ReplayRecorder] = None


def get_recorder() -> ReplayRecorder:
    """Get the process-global replay recorder."""
    global _global_recorder
    if _global_recorder is None:
        _global_recorder = ReplayRecorder()
    return _global_recorder


def reset_recorder(clock: Callable[[], float] = time.time) -> ReplayRecorder:
    """Reset and return a new global recorder (also re-reads the env flags)."""
    global _global_recorder
    _global_recorder = ReplayRecorder(clock=clock)
    return _global_recorder
