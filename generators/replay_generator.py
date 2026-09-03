"""Builds the LLM replay artifacts from a completed pipeline run.

Three files land in ``web/data/{date}/``:

* ``replay-index.json`` -- small, permanent, and self-sufficient. Everything the
  replay UI needs to draw the newsroom, the Gantt and the funnel.
* ``replay-stream.json.gz`` -- the per-call output deltas that drive the
  typewriter. Heavy, prunable, and capped; the index works without it.
* ``replay-prompts.json.gz`` -- the prompt each call actually sent. The largest of
  the three (~600 KB gzipped), fetched only when a detail pane is opened, and
  pruned after ``PROMPT_RETENTION_DAYS``. The index and stream work without it.

The two ``.gz`` files are inflated in the browser via ``DecompressionStream``
rather than by ``Content-Encoding``; nginx serves them as opaque binaries.

``docs/replay-schema.md`` is the contract. This module shapes raw material into
it from three sources, in decreasing order of fidelity:

1. the live :mod:`agents.replay_recorder` snapshot (real per-event timings),
2. the cost tracker's per-call rows (tokens, provider, profile -- always there),
3. the phase tracker (phase boundaries).

Only (2) survives to disk in ``data/processed/``, which is what makes offline
regeneration of a historical day possible -- with coarser timings, flagged as
such. Generation must never fail a pipeline run: :func:`generate_replay` catches
everything and returns ``None``.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

if __name__ == "__main__" and __package__ is None:
    # Running as a script (offline regeneration) puts generators/ on sys.path
    # rather than the repo root, so the agents package would not resolve.
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.cost_tracker import APICallRecord, CostTracker
from agents.replay_taxonomy import (
    MARQUEE_ROLES,
    ROLE_IMAGE,
    agent_for,
    agent_ids,
    resolve_call,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
GENERATED_BY = "replay_generator/1.0"

# Sampling interval for the concurrency series. 2s over a ~70min run is ~2100
# samples -- fine gzipped, and finer than the eye can follow when scrubbing.
CONCURRENCY_INTERVAL_MS = 2000

DEFAULT_MAX_STREAM_BYTES = 600_000

# Prompts are the biggest artifact -- a run sends ~2.7 MB of prompt to produce
# ~370 KB of output, because every analyzer batch carries its items. This is a
# reporting threshold rather than a hard ladder like the stream's: the file is
# fetched only when a detail pane opens, so an oversized one costs page-load
# nothing, and truncating a prompt is worse than publishing a large file.
DEFAULT_MAX_PROMPT_BYTES = 1_500_000

# Prompt artifacts older than this are pruned on each run. Retention is by age
# here (unlike the stream's size-based pruning) because the cost being managed is
# git history growth -- ~220 MB/year if kept forever -- not page weight.
PROMPT_RETENTION_DAYS = 30

# Phase names come from the orchestrator as "Phase 2.5: Continuity Detection".
_PHASE_NAME_RE = re.compile(r"^Phase\s+(?P<ordinal>[\d.]+)\s*:\s*(?P<label>.+)$")

# Sources are reported per category by the gatherers; the social gatherer also
# reports per platform. Mapping them onto the cast keeps the stage honest about
# who fetched what.
_SOURCE_LABELS = {
    "news": ("news_gatherer", "RSS feeds"),
    "research": ("research_gatherer", "arXiv + blogs"),
    "social": ("social_gatherer", "Social platforms"),
    "reddit": ("reddit_gatherer", "Reddit"),
    "social_twitter": ("social_gatherer", "Twitter"),
    "social_bluesky": ("social_gatherer", "Bluesky"),
    "social_mastodon": ("social_gatherer", "Mastodon"),
    # Timed separately from each other since 2026-07-29; on older days neither key
    # exists and the combined "research" row above is what renders.
    "research_arxiv": ("research_gatherer", "arXiv"),
    "research_blogs": ("research_gatherer", "Research blogs"),
}

# Aggregate rows would double-count against their per-source children. `research`
# is only superseded on runs that actually recorded the split -- older days have no
# children for it, and dropping it there would lose the row entirely.
_SOURCE_SUPERSEDED = {"social"}
_SOURCE_SUPERSEDED_IF_SPLIT = {"research": ("research_arxiv", "research_blogs")}

# Anything matching these in the finished artifact means something leaked that
# should not be published. Checked before writing, not after.
#
# These target credentials and infrastructure, not URLs in general: model output
# legitimately quotes links (a hero prompt summarising a story about a product
# launch, say), and failing the whole replay over a public https:// in prose
# would be a false positive that loses the day's artifact for no security gain.
# What must never appear is an API key, a bearer token, or the private endpoint
# host -- so those are matched specifically.
#
# The `sk-` rules are deliberately narrow. An unanchored `sk-[A-Za-z0-9_\-]{16,}`
# looks strict but matches inside ordinary words: on 2026-07-31 a DeepMind blog
# URL ending "...video-understanding-ta|sk-orchestration-and-multi-robot-
# collaboration" tripped it and the day's replay was lost. That is not a rare
# collision -- 120 of 218 published days carry a kebab-case slug that matches,
# so with the prompt artifact in scope the gate was a coin flip on the news.
# Requiring a word boundary and refusing hyphens in a bare key body drops the
# false-positive rate to zero across the whole corpus while still catching every
# real key shape: sk-ant- (Anthropic), sk-proj- (OpenAI), sk-or- (OpenRouter),
# sk-live-/sk-test- (Stripe-style), and bare OpenAI keys, which are long
# unbroken alphanumeric runs.
_SECRET_PATTERNS = (
    re.compile(r"\bsk-(?:ant|proj|or|live|test)-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(r"\b(?:api[_-]?key|auth[_-]?token|secret)\s*[=:]\s*\S+", re.IGNORECASE),
    # Credentials embedded in a URL, e.g. https://user:pass@host
    re.compile(r"https?://[^/\s]*:[^/\s]*@", re.IGNORECASE),
)

# Hosts that identify private infrastructure. Matched case-insensitively anywhere
# in the artifact. Kept separate from the regex list so the failure message can
# say which host leaked.
_FORBIDDEN_HOST_ENV_VARS = ("ANTHROPIC_API_BASE", "ANTHROPIC_BASE_URL", "SCRAPECREATORS_BASE")


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(f"Ignoring invalid {name}={raw!r}; using {default}")
        return default
    if value < minimum:
        logger.warning(f"Ignoring {name}={value}; minimum is {minimum}, using {default}")
        return default
    return value


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp, tolerating both naive and ``Z``-suffixed forms."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(epoch: float) -> str:
    """Epoch seconds -> naive-local ISO, the form cost-report rows already use.

    Round-trips with ``_parse_iso``/``_epoch`` so a rewritten timestamp is read
    back exactly as it was written.
    """
    return datetime.fromtimestamp(epoch).isoformat()


def _epoch(value: Optional[datetime]) -> Optional[float]:
    if value is None:
        return None
    if value.tzinfo is None:
        # Cost-report timestamps are naive local time (datetime.now()); the
        # phase tracker's are epoch floats. Interpreting naive as local keeps
        # the two consistent when both feed the same timeline.
        return value.timestamp()
    return value.timestamp()


def _phase_id(ordinal: str) -> str:
    return f"phase-{ordinal.replace('.', '-')}"


def _split_phase_name(name: str) -> Tuple[str, str]:
    match = _PHASE_NAME_RE.match(name or "")
    if match:
        return match.group("ordinal"), match.group("label").strip()
    return "", (name or "Phase").strip()


class ReplayGenerator:
    """Shapes a run into the replay artifacts.

    ``recorder_snapshot`` is optional: without it the generator still produces a
    complete index from cost/phase data, just with per-call timings derived from
    each call's end timestamp and duration rather than measured per event.
    """

    def __init__(
        self,
        web_dir: str,
        max_stream_bytes: Optional[int] = None,
        max_prompt_bytes: Optional[int] = None,
    ):
        self.web_dir = web_dir
        self.data_dir = os.path.join(web_dir, "data")
        self.max_stream_bytes = (
            max_stream_bytes
            if max_stream_bytes is not None
            else _env_int("LLM_REPLAY_MAX_BYTES", DEFAULT_MAX_STREAM_BYTES, minimum=1024)
        )
        self.max_prompt_bytes = (
            max_prompt_bytes
            if max_prompt_bytes is not None
            else _env_int("LLM_REPLAY_MAX_PROMPT_BYTES", DEFAULT_MAX_PROMPT_BYTES, minimum=1024)
        )

    # -- timebase --------------------------------------------------------

    def _resolve_t0(
        self,
        recorder: Optional[Dict[str, Any]],
        cost_report: Dict[str, Any],
        phase_records: Sequence[Dict[str, Any]],
    ) -> Tuple[float, bool]:
        """Pick the run's origin. Returns ``(epoch_seconds, measured)``.

        The recorder's t0 is authoritative when present because every delta was
        stamped against it. Otherwise the earliest of the cost report's start
        and the first phase start wins.
        """
        if recorder and recorder.get("t0_epoch"):
            return float(recorder["t0_epoch"]), True

        candidates: List[float] = []
        start = _epoch(_parse_iso(cost_report.get("start_time")))
        if start is not None:
            candidates.append(start)
        for record in phase_records:
            if record.get("start_time"):
                candidates.append(float(record["start_time"]))
        if candidates:
            return min(candidates), False

        # Nothing usable: anchor at the first call so offsets stay non-negative.
        calls = cost_report.get("calls") or []
        for row in calls:
            stamp = _epoch(_parse_iso(row.get("timestamp")))
            if stamp is not None:
                return stamp, False
        return 0.0, False

    # -- phases ----------------------------------------------------------

    def _build_phases(
        self,
        phase_records: Sequence[Dict[str, Any]],
        t0: float,
        run_end_ms: int,
        pre_flags: Optional[Sequence[bool]] = None,
    ) -> List[Dict[str, Any]]:
        """Shape phases, reconstructing boundaries when absolute times are gone.

        ``PhaseTracker.to_dict()`` keeps absolute start/end alongside duration,
        but a resumed run restores checkpoint-loaded phases with windows from
        BEFORE this process's t0. Records flagged pre-run are placed
        sequentially at their ordinal position using their real durations, and
        every in-run record is shifted by the accumulated pre-run span so the
        single timeline stays monotonic and call containment stays exact.
        """
        if pre_flags is None:
            pre_flags = [False] * len(phase_records)
        phases: List[Dict[str, Any]] = []
        cursor_ms = 0
        # Inserted pre-run duration so far -- NOT cursor_ms, which also grows
        # with live phases; live records must shift by restored spans only.
        pre_span_ms = 0
        for record, is_pre in zip(phase_records, pre_flags):
            ordinal, label = _split_phase_name(record.get("name", ""))
            duration = float(record.get("duration") or 0.0)

            if is_pre:
                # Ran in an earlier process: sequence it after whatever came
                # before on the merged timeline.
                start_ms = cursor_ms
                end_ms = start_ms + int(round(duration * 1000))
                cursor_ms = end_ms
                pre_span_ms += int(round(duration * 1000))
            elif record.get("start_time"):
                start_ms = (
                    int(round((float(record["start_time"]) - t0) * 1000))
                    + pre_span_ms
                )
                end_source = record.get("end_time")
                end_ms = (
                    int(round((float(end_source) - t0) * 1000)) + pre_span_ms
                    if end_source
                    else start_ms + int(round(duration * 1000))
                )
                start_ms = max(0, start_ms)
                end_ms = max(start_ms, end_ms)
                cursor_ms = max(cursor_ms, end_ms)
            else:
                start_ms = cursor_ms
                end_ms = start_ms + int(round(duration * 1000))
                cursor_ms = max(cursor_ms, end_ms)

            phases.append(
                {
                    "id": _phase_id(ordinal) if ordinal else f"phase-{len(phases)}",
                    "label": label,
                    "ordinal": ordinal,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "status": record.get("status") or "success",
                    "detail": record.get("details"),
                    "error": record.get("error"),
                }
            )

        # A run's final phase can end before the last LLM call settles (assembly
        # and output happen after). Stretch it so no call falls off the rail.
        if phases and run_end_ms > phases[-1]["end_ms"]:
            phases[-1]["end_ms"] = run_end_ms
        return phases

    @staticmethod
    def _phase_for(call_start_ms: int, phases: Sequence[Dict[str, Any]]) -> Optional[str]:
        """Containment lookup, falling back to the nearest preceding phase."""
        for phase in phases:
            if phase["start_ms"] <= call_start_ms <= phase["end_ms"]:
                return phase["id"]
        preceding = [p for p in phases if p["start_ms"] <= call_start_ms]
        if preceding:
            return preceding[-1]["id"]
        return phases[0]["id"] if phases else None

    # -- calls -----------------------------------------------------------

    def _merge_restored_calls(
        self,
        restored: Optional[Dict[str, Any]],
        cost_report: Dict[str, Any],
        recorder: Optional[Dict[str, Any]],
        records: Sequence[Dict[str, Any]],
        pre_flags: Sequence[bool],
        phases: Sequence[Dict[str, Any]],
        run_origin: float,
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], int]:
        """Fold a checkpoint's calls into this run's cost rows and spans.

        Returns ``(cost_report, recorder, restored_count)`` -- copies, never the
        caller's objects, so a merge cannot leak into the cost report the run
        prints.

        Placement is exact rather than sequenced. Each restored phase record
        keeps its ORIGINAL absolute window (`start_time` from `_phase_timings`)
        while `_build_phases` has just placed it somewhere on the merged
        timeline, so a call at absolute epoch E inside phase P lands at
        ``P.start_ms + (E - P.start_time) * 1000``. Containment holds by
        construction, and every value is a real measurement from the original
        process -- the same reasoning that lets pre-run gatherer spans be
        rebased instead of dropped.

        Never raises: a replay is a bonus, and a malformed bundle must degrade
        to "no restored calls" rather than lose the whole artifact.
        """
        rows = list((restored or {}).get("cost_calls") or [])
        spans = list((restored or {}).get("spans") or [])
        if not rows and not spans:
            return cost_report, recorder, 0

        try:
            # Absolute window of each restored phase -> its merged placement.
            windows: List[Tuple[float, float, int]] = []
            for record, is_pre, phase in zip(records, pre_flags, phases):
                if not is_pre or not record.get("start_time"):
                    continue
                origin = float(record["start_time"])
                duration = float(record.get("duration") or 0.0)
                windows.append((origin, origin + duration, int(phase.get("start_ms") or 0)))
            if not windows:
                return cost_report, recorder, 0

            first_origin, _, first_base = windows[0]

            def to_merged_ms(epoch: Optional[float]) -> Optional[int]:
                if epoch is None:
                    return None
                for origin, end, base in windows:
                    if origin <= epoch <= end:
                        return max(0, base + int(round((epoch - origin) * 1000)))
                # Outside every restored window (clock skew, a call straddling a
                # boundary): anchor to the first restored phase rather than
                # inventing a position elsewhere on the timeline.
                return max(0, first_base + int(round((epoch - first_origin) * 1000)))

            # Cost rows carry absolute ISO timestamps. _build_calls positions a
            # row by its timestamp relative to run_origin, so rewrite each one to
            # the epoch that lands it where the merged timeline wants it.
            merged_rows: List[Dict[str, Any]] = []
            for row in rows:
                row = dict(row)
                epoch = _epoch(_parse_iso(row.get("timestamp")))
                placed = to_merged_ms(epoch)
                if placed is not None:
                    row["timestamp"] = _iso(run_origin + placed / 1000.0)
                row["restored"] = True
                merged_rows.append(row)

            span_t0 = (restored or {}).get("t0_epoch")
            merged_spans: List[Dict[str, Any]] = []
            for span in spans:
                span = dict(span)
                if span_t0:
                    for field in ("queued_ms", "start_ms", "end_ms", "first_token_ms"):
                        value = span.get(field)
                        if value is None:
                            continue
                        placed = to_merged_ms(float(span_t0) + float(value) / 1000.0)
                        if placed is not None:
                            span[field] = placed
                span["restored"] = True
                merged_spans.append(span)

            cost_report = dict(cost_report)
            cost_report["calls"] = merged_rows + list(cost_report.get("calls") or [])

            base_recorder = dict(recorder or {})
            base_recorder["calls"] = merged_spans + list(base_recorder.get("calls") or [])
            recorder = base_recorder

            return cost_report, recorder, len(merged_rows)
        except Exception as error:  # noqa: BLE001 -- never lose the replay over this
            logger.warning(
                f"Could not merge restored replay calls "
                f"({type(error).__name__}: {error}); resumed phases will be empty"
            )
            return cost_report, recorder, 0

    def _build_calls(
        self,
        cost_report: Dict[str, Any],
        recorder: Optional[Dict[str, Any]],
        tracker: CostTracker,
        t0: float,
        phases: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Merge cost rows with recorder spans into schema call objects.

        Cost rows are the spine: every completed LLM call has one, in order.
        Recorder spans add measured queue/first-token timings and stream
        presence. They are matched positionally per caller -- the recorder
        assigns ids in call order and the cost tracker appends in completion
        order, so pairing by caller keeps concurrent calls from swapping.
        """
        recorded_by_caller: Dict[str, List[Dict[str, Any]]] = {}
        if recorder:
            for span in recorder.get("calls") or []:
                recorded_by_caller.setdefault(span.get("caller") or "", []).append(span)
            for spans in recorded_by_caller.values():
                spans.sort(key=lambda s: s.get("queued_ms") or 0)

        calls: List[Dict[str, Any]] = []
        for row in cost_report.get("calls") or []:
            caller = row.get("caller") or "unknown"
            identity = resolve_call(caller)

            # Pair the row with a span of matching disposition.
            #
            # A row marked `partial` came from a call that failed mid-stream (its
            # tokens were read off the SSE events), so it belongs to a *failed*
            # span. Every other row belongs to a span that succeeded.
            #
            # Getting this wrong is what produced the 2026-07-28 bug: with naive
            # positional pairing the failed span was handed to the retry's cost row,
            # so the call rendered "failed" while carrying the successful attempt's
            # tokens, duration and cost -- five batches the pipeline had logged as
            # 5/5 and 7/7 successful. Spans passed over here are not discarded; any
            # left unmatched are emitted below as attempts in their own right.
            row_is_partial = bool(row.get("partial"))
            span: Optional[Dict[str, Any]] = None
            queue = recorded_by_caller.get(caller)
            if queue:
                for i, candidate in enumerate(queue):
                    failed = (candidate.get("outcome") or "ok") in ("failed", "refused")
                    if failed == row_is_partial:
                        span = queue.pop(i)
                        break

            duration_ms = int(round(float(row.get("duration_seconds") or 0.0) * 1000))
            if span and span.get("end_ms") is not None:
                end_ms = int(span["end_ms"])
                start_ms = int(span.get("start_ms") or end_ms - duration_ms)
                queued_ms = int(span.get("queued_ms") or start_ms)
                wait_ms = int(span.get("wait_ms") or max(0, start_ms - queued_ms))
                first_token_ms = span.get("first_token_ms")
            else:
                # Offline path: the cost row's timestamp is when the call was
                # *recorded*, i.e. on completion. Work backwards for the start.
                finished = _epoch(_parse_iso(row.get("timestamp")))
                end_ms = int(round((finished - t0) * 1000)) if finished is not None else duration_ms
                start_ms = max(0, end_ms - duration_ms)
                queued_ms = start_ms
                wait_ms = 0
                first_token_ms = None

            record = APICallRecord(
                timestamp=row.get("timestamp") or "",
                caller=caller,
                thinking_level=row.get("thinking_level"),
                input_tokens=int(row.get("input_tokens") or 0),
                output_tokens=int(row.get("output_tokens") or 0),
                cache_creation_tokens=int(row.get("cache_creation_tokens") or 0),
                cache_read_tokens=int(row.get("cache_read_tokens") or 0),
                model=row.get("model") or "",
            )
            cost = tracker.calculate_cost(record)

            context = (span or {}).get("context") or {}
            deltas = (span or {}).get("deltas") or {}
            has_stream = bool(deltas.get("t"))

            calls.append(
                {
                    "id": (span or {}).get("id") or f"c{len(calls) + 1:03d}",
                    "agent_id": identity.agent_id,
                    "phase_id": self._phase_for(start_ms, phases),
                    "caller": caller,
                    "task": identity.task,
                    "role": identity.role,
                    "worker": identity.worker,
                    "queued_ms": queued_ms,
                    "start_ms": start_ms,
                    "first_token_ms": first_token_ms,
                    "end_ms": end_ms,
                    "wait_ms": wait_ms,
                    "provider_id": row.get("provider_id") or "unknown",
                    "model": row.get("model") or "",
                    "profile": row.get("analysis_profile") or "STANDARD",
                    "effort": row.get("adaptive_effort") or "high",
                    "input_tokens": record.input_tokens,
                    "output_tokens": record.output_tokens,
                    "cache_read_tokens": record.cache_read_tokens,
                    "cost_usd": round(cost.total_cost, 6),
                    "thinking_chars": int((span or {}).get("thinking_chars") or 0),
                    "text_chars": int((span or {}).get("text_chars") or 0),
                    "stream_events": int((span or {}).get("delta_events") or 0),
                    "stop_reason": (span or {}).get("stop_reason"),
                    "outcome": (span or {}).get("outcome") or "ok",
                    # False only for a call that failed mid-stream: its tokens are
                    # real and billed, but read off the SSE events, so they are a
                    # floor rather than an exact figure.
                    "billed_exact": not row_is_partial,
                    "attempt": int(context.get("attempt") or 1),
                    "fallback_from": context.get("fallback_from"),
                    "retry_reason": context.get("retry_reason"),
                    "has_stream": has_stream,
                }
            )

        # Attempts with no cost row at all.
        #
        # Since partial-spend recording landed, a call that streams anything before
        # dying does produce a row (`partial: true`), so this path now only catches
        # attempts that failed before the first `message_delta` -- a connection
        # refused, an immediate 4xx -- plus any run generated before that change.
        # Those genuinely have no token counts to recover.
        #
        # They carry zero tokens and `billed: false`, meaning "cost unknown", not
        # "free". The UI must not render them as $0.00. Dropping them entirely (the
        # original behaviour) was worse: a retried batch looked like one clean call,
        # hiding both the failure and the recovery.
        for caller, leftovers in recorded_by_caller.items():
            identity = resolve_call(caller)
            for span in leftovers:
                if span.get("end_ms") is None:
                    continue
                end_ms = int(span["end_ms"])
                start_ms = int(span.get("start_ms") or end_ms)
                queued_ms = int(span.get("queued_ms") or start_ms)
                context = span.get("context") or {}
                deltas = span.get("deltas") or {}
                calls.append(
                    {
                        "id": span.get("id") or f"c{len(calls) + 1:03d}",
                        "agent_id": identity.agent_id,
                        "phase_id": self._phase_for(start_ms, phases),
                        "caller": caller,
                        "task": identity.task,
                        "role": identity.role,
                        "worker": identity.worker,
                        "queued_ms": queued_ms,
                        "start_ms": start_ms,
                        "first_token_ms": span.get("first_token_ms"),
                        "end_ms": end_ms,
                        "wait_ms": int(span.get("wait_ms") or max(0, start_ms - queued_ms)),
                        "provider_id": context.get("provider_id") or "unknown",
                        "model": context.get("provider_model") or "",
                        "profile": context.get("analysis_profile") or "STANDARD",
                        "effort": context.get("adaptive_effort") or "high",
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_tokens": 0,
                        "cost_usd": 0.0,
                        "billed": False,
                        "thinking_chars": int(span.get("thinking_chars") or 0),
                        "text_chars": int(span.get("text_chars") or 0),
                        "stream_events": int(span.get("delta_events") or 0),
                        "stop_reason": span.get("stop_reason"),
                        "outcome": span.get("outcome") or "failed",
                        "error_type": span.get("error_type"),
                        "attempt": int(context.get("attempt") or 1),
                        "fallback_from": context.get("fallback_from"),
                        "retry_reason": context.get("retry_reason"),
                        "has_stream": bool(deltas.get("t")),
                    }
                )

        # Link each failed attempt to the attempt that recovered it, so the UI can
        # say "retried, succeeded" rather than leaving a bare red row that reads as
        # lost data. A failure with no later success stays unlinked -- that one
        # really did lose its batch.
        by_caller: Dict[str, List[Dict[str, Any]]] = {}
        for call in calls:
            by_caller.setdefault(call["caller"], []).append(call)
        for group in by_caller.values():
            group.sort(key=lambda c: c["start_ms"])
            for i, call in enumerate(group):
                if call["outcome"] not in ("failed", "refused"):
                    continue
                successor = next(
                    (s for s in group[i + 1:] if s["outcome"] not in ("failed", "refused")),
                    None,
                )
                if successor is not None:
                    call["recovered_by"] = successor["id"]
                    successor["recovers"] = call["id"]
                    self._estimate_failed_input(call, successor, tracker)

        calls.sort(key=lambda c: (c["start_ms"], c["id"]))
        return calls

    @staticmethod
    def _estimate_failed_input(
        call: Dict[str, Any], successor: Dict[str, Any], tracker: CostTracker
    ) -> None:
        """Attribute a timed-out attempt's input cost from its retry.

        A request that dies before the provider's first SSE event leaves nothing to
        measure -- not even a zero. But the prompt was still ingested and still
        charged, so reporting $0.000 states the one thing we know to be false. The
        retry sends the *same* prompt, so its input token count is a sound estimate
        of what the failed attempt cost.

        This is the only inferred figure in the artifact, and it is flagged as such:
        `input_estimated` marks it, and the UI must never present it as measured.
        Deliberately narrow, so the flag stays meaningful:

        * only when nothing at all was measured (a call that streamed before dying
          has real numbers from the stream and keeps them),
        * only input -- output is genuinely unknowable, since the model never wrote,
        * only from a same-caller retry, which is the only case where the prompt is
          known to be identical.

        The estimate stays out of the run total, which remains a sum of measured
        spend. Surfacing it per call is the point; silently inflating the headline
        figure with an inference is not.
        """
        if call.get("input_tokens") or call.get("output_tokens"):
            return  # something was measured; never overwrite it with a guess
        if call.get("text_chars"):
            return  # it wrote, so the stream carried usage worth trusting
        inherited = successor.get("input_tokens") or 0
        if inherited <= 0:
            return
        call["input_tokens_estimated"] = inherited
        call["cost_usd_estimated"] = round((inherited / 1_000_000) * tracker.input_price, 6)

    # -- derived series --------------------------------------------------

    @staticmethod
    def _concurrency(
        calls: Sequence[Dict[str, Any]], duration_ms: int, interval_ms: int
    ) -> Tuple[Dict[str, Any], int]:
        """Sample active/queued counts over the run.

        A call is *active* between start and end, and *queued* between queue
        time and start. Sampling beats an event sweep here because the frontend
        wants a fixed-step series to plot anyway.
        """
        samples: List[List[int]] = []
        peak = 0
        if duration_ms <= 0:
            return {"interval_ms": interval_ms, "samples": samples}, peak

        # Sorted copies let each step scan only the calls that can still match.
        by_start = sorted(calls, key=lambda c: c["start_ms"])
        for t_ms in range(0, duration_ms + interval_ms, interval_ms):
            active = 0
            queued = 0
            for call in by_start:
                if call["start_ms"] > t_ms:
                    # Still sorted by start, so nothing later can be active --
                    # but it may already be queued.
                    if call["queued_ms"] <= t_ms:
                        queued += 1
                    continue
                if call["end_ms"] >= t_ms:
                    active += 1
            peak = max(peak, active)
            samples.append([t_ms, active, queued])
        return {"interval_ms": interval_ms, "samples": samples}, peak

    @staticmethod
    def _build_sources(
        collection_status: Dict[str, Any],
        phases: Sequence[Dict[str, Any]],
        t0: Optional[float] = None,
        rebase: Optional[Tuple[float, int]] = None,
    ) -> List[Dict[str, Any]]:
        """Turn per-source collection results into stage props.

        Gatherers record their own wall-clock span per source (``started_at`` /
        ``ended_at`` epochs, same convention as ``PhaseTracker``), which becomes a
        real staggered bar. When a source has no timing -- an un-instrumented
        gatherer, or a day published before this existed -- it falls back to the
        gathering phase's span and is flagged ``timing_measured: false`` so the UI
        can draw it as an estimate rather than passing it off as a measurement.

        The fallback is per source, not per run: one stale row must not downgrade
        the rows that were genuinely measured.
        """
        gathering = next((p for p in phases if p["ordinal"] == "1"), None)
        fallback_start = gathering["start_ms"] if gathering else 0
        fallback_end = gathering["end_ms"] if gathering else 0

        status = collection_status or {}
        superseded = set(_SOURCE_SUPERSEDED)
        # Only drop an aggregate row when its children are actually present.
        for parent, children in _SOURCE_SUPERSEDED_IF_SPLIT.items():
            if any(c in status for c in children):
                superseded.add(parent)

        def offsets(value: Dict[str, Any]) -> Tuple[int, int, bool]:
            """Real ms offsets for one source, or the phase span if unmeasured."""
            started = value.get("started_at")
            ended = value.get("ended_at")
            if t0 is None or not isinstance(started, (int, float)) or not isinstance(
                ended, (int, float)
            ):
                return fallback_start, fallback_end, False
            start_ms = int(round((float(started) - t0) * 1000))
            end_ms = int(round((float(ended) - t0) * 1000))
            # A resumed run replays gathering from a checkpoint written under an
            # *earlier* t0: those epochs predate this run's origin and would land
            # at large negative offsets. They are still real measurements -- rebase
            # them into the restored gathering window preserving relative stagger
            # instead of discarding them (2026-08-22: dropping them stripped the
            # per-subreddit / per-chunk detail from every resumed day).
            if start_ms < 0 or end_ms < start_ms:
                if rebase is not None:
                    anchor, base_ms = rebase
                    shifted = int(round((float(started) - anchor) * 1000))
                    span = max(0, int(round((float(ended) - float(started)) * 1000)))
                    return (
                        base_ms + max(0, shifted),
                        base_ms + max(0, shifted) + span,
                        True,
                    )
                return fallback_start, fallback_end, False
            return start_ms, end_ms, True

        sources: List[Dict[str, Any]] = []
        for key, value in status.items():
            if key in superseded:
                continue
            mapping = _SOURCE_LABELS.get(key)
            if not mapping:
                continue
            if not isinstance(value, dict):
                continue
            agent_id, label = mapping
            start_ms, end_ms, measured = offsets(value)
            row = {
                "agent_id": agent_id,
                "name": label,
                "items": int(value.get("count") or 0),
                "status": value.get("status") or "success",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "timing_measured": measured,
            }
            steps = ReplayGenerator._build_steps(value.get("steps"), t0, rebase)
            if steps:
                row["steps"] = steps
                dropped = value.get("steps_dropped")
                # Capture-side overflow plus anything this pass discarded. Reported
                # so a short bar reads as truncated rather than as complete.
                unresolved = len(value.get("steps") or []) - len(steps)
                total_dropped = int(dropped or 0) + max(0, unresolved)
                if total_dropped:
                    row["steps_dropped"] = total_dropped
            sources.append(row)
        return sources

    @staticmethod
    def _build_steps(
        raw_steps: Any,
        t0: Optional[float],
        rebase: Optional[Tuple[float, int]] = None,
    ) -> List[Dict[str, Any]]:
        """Convert one source's per-unit spans into ms offsets.

        A step whose epochs do not resolve against this run's ``t0`` is normally
        **dropped, not clamped** -- the same rule the parent row follows. With a
        ``rebase`` anchor (resumed runs), pre-t0 steps are real measurements from
        the original process: they are shifted into the restored gathering window
        preserving relative stagger rather than discarded.

        Unlike the parent row there is no phase-span fallback: a step exists to say
        "this unit came back at this moment", and a step without that says nothing
        worth drawing.
        """
        if not isinstance(raw_steps, list) or t0 is None:
            return []

        steps: List[Dict[str, Any]] = []
        for entry in raw_steps:
            if not isinstance(entry, dict):
                continue
            started = entry.get("started_at")
            ended = entry.get("ended_at")
            if not isinstance(started, (int, float)) or not isinstance(ended, (int, float)):
                continue
            start_ms = int(round((float(started) - t0) * 1000))
            end_ms = int(round((float(ended) - t0) * 1000))
            if (start_ms < 0 or end_ms < start_ms) and rebase is not None:
                anchor, base_ms = rebase
                shifted = max(0, int(round((float(started) - anchor) * 1000)))
                span = max(0, int(round((float(ended) - float(started)) * 1000)))
                start_ms, end_ms = base_ms + shifted, base_ms + shifted + span
            elif start_ms < 0 or end_ms < start_ms:
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            steps.append(
                {
                    "name": name[:64],
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "items": int(entry.get("items") or 0),
                    "status": entry.get("status") or "success",
                }
            )

        # Completion order is what the frontend counts against, and a thread pool
        # finishes out of dispatch order. Sorting by end keeps the step function
        # monotonic without the frontend having to re-sort on every frame.
        steps.sort(key=lambda s: (s["end_ms"], s["start_ms"]))
        return steps

    @staticmethod
    def _hero_call(
        orchestrator_result: Dict[str, Any], phases: Sequence[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Synthesize the Illustrator's call from the hero phase and its output.

        Hero generation goes through a separate image client that never touches
        the cost tracker, so it produces no row for :meth:`_build_calls` to find.
        Without this the replay shows a Hero Image phase lasting a minute with
        nobody doing anything -- the one visible step whose result you can
        actually look at, missing from the cast.

        Everything here is read from what the pipeline already recorded: the
        phase bounds for timing, and summary-level fields for the image and the
        prompt. Nothing about image generation is re-run or altered.
        """
        phase = next((p for p in phases if p["ordinal"] == "4.7"), None)
        if phase is None or phase.get("status") == "skipped":
            return None

        image_url = orchestrator_result.get("hero_image_url")
        prompt = orchestrator_result.get("hero_image_prompt")
        if not image_url and not prompt:
            # Phase ran but produced nothing recoverable (image provider absent,
            # or an older run predating these fields). A station with no result
            # is worse than none.
            return None

        # The image model is not an LLM route, so it never reaches the cost tracker.
        # When the provider reported usage the pipeline saved it, and it is a real
        # measurement worth showing; when it did not, these stay zero and the UI
        # falls back to "n/a" on the strength of `usage_measured`.
        usage = orchestrator_result.get("hero_image_usage") or {}
        image_tokens = int(usage.get("image_tokens") or 0)
        text_tokens = int(usage.get("text_tokens") or 0)

        return {
            "id": "hero",
            "agent_id": "hero",
            "phase_id": phase["id"],
            "caller": "hero_generator.compose",
            "task": "Paint the day's scene",
            "role": ROLE_IMAGE,
            "worker": None,
            "queued_ms": phase["start_ms"],
            "start_ms": phase["start_ms"],
            "first_token_ms": None,
            "end_ms": phase["end_ms"],
            "wait_ms": 0,
            "provider_id": "image",
            "model": usage.get("model") or "gemini-3-pro-image",
            "profile": "STANDARD",
            "effort": "high",
            "input_tokens": int(usage.get("input_tokens") or 0),
            # Both classes are output; the split is preserved below because they
            # bill at very different rates and the difference is the interesting part.
            "output_tokens": image_tokens + text_tokens,
            "image_tokens": image_tokens,
            "cache_read_tokens": 0,
            "cost_usd": float(usage.get("cost_usd") or 0.0),
            "usage_measured": bool(usage),
            "thinking_chars": 0,
            "text_chars": len(prompt or ""),
            "stream_events": 0,
            "stop_reason": "end_turn",
            "outcome": "ok" if phase.get("status") == "success" else "failed",
            "attempt": 1,
            "fallback_from": None,
            "retry_reason": None,
            "has_stream": False,
            # Replay-only extras. The frontend renders the finished image and the
            # prompt that produced it, which makes this the one call in the run
            # whose output you can see rather than read.
            "image_url": image_url,
            "image_prompt": prompt,
        }

    @staticmethod
    def _unbilled_image_spend(
        cost_report: Dict[str, Any], calls: Sequence[Dict[str, Any]]
    ) -> float:
        """Image spend the run's own cost report does not already contain.

        Hero generation goes through the image client, which never reaches the
        CostTracker, so ``cost["total"]`` covers LLM routes only. The replay
        nonetheless shows the image call and its price, so a header taken
        straight from that total can read *less* than a line item on the same
        page: 2026-08-27 published a $0.135 run header above a $0.138 hero card.

        Nothing is invented here. The figure is the one the hero call already
        carries, which is exactly zero when the provider reported no usage
        (``usage_measured: false``) -- and adding zero leaves unknown unknown.

        If the tracker ever records image rows itself, the report already
        carries that spend and adding it again would double-charge, so such a
        report is left alone. (A report of that shape would additionally need
        :meth:`_build_calls` to skip its image rows, or the hero would be drawn
        twice -- once from the row, once synthesized from ``hero_image_usage``.)
        """
        cost = cost_report.get("cost") or {}
        if "image" in cost:
            return 0.0
        if any(
            isinstance(row, dict) and row.get("kind") == "image"
            for row in cost_report.get("calls") or []
        ):
            return 0.0
        return sum(
            float(call.get("cost_usd") or 0.0)
            for call in calls
            if call.get("role") == ROLE_IMAGE
        )

    @staticmethod
    def _build_agents(
        calls: Sequence[Dict[str, Any]], sources: Sequence[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Roll calls up per agent, keeping the cast in stage order."""
        rollup: Dict[str, Dict[str, Any]] = {}
        for call in calls:
            entry = rollup.setdefault(
                call["agent_id"],
                {
                    "call_count": 0,
                    "cost_usd": 0.0,
                    "output_tokens": 0,
                    "phase_ids": [],
                    "failed": 0,
                },
            )
            entry["call_count"] += 1
            entry["cost_usd"] += call["cost_usd"]
            entry["output_tokens"] += call["output_tokens"]
            if call["phase_id"] and call["phase_id"] not in entry["phase_ids"]:
                entry["phase_ids"].append(call["phase_id"])
            if call["outcome"] in ("failed", "refused"):
                entry["failed"] += 1

        collected: Dict[str, int] = {}
        for source in sources:
            collected[source["agent_id"]] = collected.get(source["agent_id"], 0) + source["items"]

        # Preserve the taxonomy's stage order, then append anything unexpected.
        ordered = [a for a in agent_ids() if a in rollup or a in collected]
        ordered += [a for a in rollup if a not in ordered]

        agents: List[Dict[str, Any]] = []
        for agent_id in ordered:
            identity = agent_for(agent_id)
            entry = rollup.get(agent_id, {})
            items_in = collected.get(agent_id)
            agents.append(
                {
                    "id": identity.id,
                    "label": identity.label,
                    "kind": identity.kind,
                    "category": identity.category,
                    "phase_ids": entry.get("phase_ids", []),
                    "call_count": entry.get("call_count", 0),
                    "cost_usd": round(entry.get("cost_usd", 0.0), 6),
                    "output_tokens": entry.get("output_tokens", 0),
                    "items_in": items_in,
                    "items_out": None,
                    "status": "partial" if entry.get("failed") else "success",
                    "blurb": identity.blurb or None,
                }
            )
        return agents

    # -- stream artifact -------------------------------------------------

    def _build_prompts(
        self,
        recorder: Optional[Dict[str, Any]],
        calls: List[Dict[str, Any]],
        date: str,
    ) -> Optional[bytes]:
        """Gzip the per-call prompts into their own artifact.

        Separate from the index because it is by far the largest thing published
        (~600 KB gzipped vs the index's ~55 KB): the index loads on every page
        view, while this is fetched only when a detail pane is opened.

        The hero is folded in here too. Its "call" is synthesized by
        :meth:`_hero_call` rather than recorded, so it never passes through the
        recorder -- routing it through the same map means the UI has exactly one
        place to look for any call's prompt.

        Returns ``None`` when nothing was captured, which the frontend renders as
        "not retained for this date".
        """
        out: Dict[str, Any] = {}

        for span in (recorder or {}).get("calls") or []:
            prompt = span.get("prompt") or {}
            system = prompt.get("system")
            messages = prompt.get("messages")
            if not system and not messages:
                continue
            entry: Dict[str, Any] = {}
            if system:
                entry["system"] = system
            if messages:
                entry["messages"] = messages
            if span.get("prompt_chars"):
                entry["chars"] = span["prompt_chars"]
            if span.get("prompt_truncated"):
                entry["truncated"] = True
            out[span["id"]] = entry

        # The image prompt already rides on the call itself (it is what the
        # Illustrator's pane renders); mirror it so both paths agree.
        for call in calls:
            prompt = call.get("image_prompt")
            if prompt and call["id"] not in out:
                out[call["id"]] = {"messages": prompt, "chars": len(prompt)}

        if not out:
            return None

        document = {"schema": SCHEMA_VERSION, "date": date, "calls": out}
        raw = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        blob = gzip.compress(raw, compresslevel=9)

        # Prompts are already capped per call and per run by the recorder, so this
        # is a backstop that reports rather than degrades: losing prompts is far
        # less bad than losing the timeline, and the file is independently
        # fetched, so an oversized one costs nothing until someone opens a pane.
        if len(blob) > self.max_prompt_bytes:
            logger.warning(
                f"Replay prompts artifact is {len(blob)} bytes, over the "
                f"{self.max_prompt_bytes} byte guidance; publishing anyway "
                "(it is lazily fetched, so it does not slow the page)."
            )
        return blob

    def _build_stream(
        self, recorder: Optional[Dict[str, Any]], calls: List[Dict[str, Any]], date: str
    ) -> Tuple[Optional[bytes], Optional[str]]:
        """Compress the delta arrays, shrinking until they fit the cap.

        Returns ``(gzip_bytes, truncation_note)``. The ladder degrades what is
        stored rather than silently dropping the tail of a call, so whatever
        survives is complete for the calls it covers.
        """
        if not recorder:
            return None, None
        spans = {
            span["id"]: span
            for span in (recorder.get("calls") or [])
            if (span.get("deltas") or {}).get("t")
        }
        if not spans:
            return None, None

        marquee = {c["id"] for c in calls if c["role"] in MARQUEE_ROLES}

        def payload(
            coalesce_ms: int, thinking_only: Sequence[str] = (), keep: Optional[set] = None
        ) -> Tuple[bytes, Dict[str, Any]]:
            out: Dict[str, Any] = {}
            for call_id, span in spans.items():
                if keep is not None and call_id not in keep:
                    continue
                deltas = span["deltas"]
                t_list, kind_list, text_list = [], [], []
                drop_text = call_id in thinking_only
                for stamp, kind, text in zip(deltas["t"], deltas["kind"], deltas["text"]):
                    if drop_text and kind == 1:
                        continue
                    # Re-coalesce at a coarser window by merging into the last
                    # entry when it is same-kind and inside the window.
                    if t_list and kind_list[-1] == kind and (stamp - t_list[-1]) < coalesce_ms:
                        text_list[-1] += text
                        continue
                    t_list.append(stamp)
                    kind_list.append(kind)
                    text_list.append(text)
                if t_list:
                    out[call_id] = {"t": t_list, "kind": kind_list, "text": text_list}
            document = {"schema": SCHEMA_VERSION, "date": date, "calls": out}
            raw = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            return gzip.compress(raw, compresslevel=9), out

        ladder: List[Tuple[str, Dict[str, Any]]] = [
            ("none", {"coalesce_ms": 0}),
            ("coalesced_250ms", {"coalesce_ms": 250}),
            (
                "text_dropped_for_minor_calls",
                {
                    "coalesce_ms": 250,
                    "thinking_only": [i for i in spans if i not in marquee],
                },
            ),
            ("marquee_only", {"coalesce_ms": 250, "keep": marquee}),
        ]

        for note, kwargs in ladder:
            blob, kept = payload(**kwargs)
            if len(blob) <= self.max_stream_bytes:
                if note != "none":
                    logger.warning(
                        f"Replay stream exceeded {self.max_stream_bytes} bytes; "
                        f"applied '{note}' ({len(blob)} bytes, {len(kept)} calls)"
                    )
                    for call in calls:
                        call["has_stream"] = call["id"] in kept
                return blob, (None if note == "none" else note)

        # Even the narrowest rung overflows: keep it and say so rather than
        # publish nothing.
        blob, kept = payload(coalesce_ms=250, keep=marquee)
        logger.warning(
            f"Replay stream still {len(blob)} bytes after full degradation ladder; "
            "publishing marquee-only anyway"
        )
        for call in calls:
            call["has_stream"] = call["id"] in kept
        return blob, "marquee_only_over_cap"

    # -- safety ----------------------------------------------------------

    @staticmethod
    def _assert_publishable(index: Dict[str, Any], what: str = "index") -> None:
        """Refuse to write anything that carries a credential or private host.

        The artifact is published publicly, so this is the last gate before it
        lands on disk. It deliberately does *not* reject URLs in general --
        prompts and model output both quote public links legitimately, and losing
        a day's replay over that would be a false positive with no security
        benefit. Credentials, bearer tokens, key-value secrets and the private
        endpoint hosts are what must never appear.

        Runs over the prompt artifact as well as the index. That is where this
        check earns its keep: prompts are assembled from config and collected
        data, so unlike the index they are not a fixed set of known fields.
        """
        blob = json.dumps(index, ensure_ascii=False)
        for pattern in _SECRET_PATTERNS:
            match = pattern.search(blob)
            if match:
                raise ValueError(
                    f"Replay {what} contains disallowed content matching {pattern.pattern!r}: "
                    f"{match.group(0)[:40]!r}"
                )

        # The configured endpoints are the one host family that is genuinely
        # sensitive here; compare against the live values rather than hardcoding.
        lowered = blob.lower()
        for var in _FORBIDDEN_HOST_ENV_VARS:
            raw = (os.environ.get(var) or "").strip()
            if not raw:
                continue
            host = raw.split("://", 1)[-1].split("/", 1)[0].strip().lower()
            # Ignore loopback and empty hosts: they identify nothing.
            if not host or host.startswith("localhost") or host.startswith("127."):
                continue
            if host in lowered:
                raise ValueError(
                    f"Replay {what} leaks the {var} host ({host!r}); refusing to write"
                )

    # -- entry point -----------------------------------------------------

    def build(
        self,
        date: str,
        cost_report: Dict[str, Any],
        orchestrator_result: Dict[str, Any],
        recorder_snapshot: Optional[Dict[str, Any]] = None,
        phase_records: Optional[Sequence[Dict[str, Any]]] = None,
        restored_replay: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], Optional[bytes]]:
        """Shape a run into ``(index, stream_gzip_or_None)``."""
        recorder = recorder_snapshot if (recorder_snapshot or {}).get("calls") else None
        records = list(phase_records or orchestrator_result.get("phase_status") or [])

        # A resumed run restores checkpoint-loaded phases whose absolute
        # windows predate this process. Split them out: they get sequenced
        # onto the front of the timeline, and everything measured in THIS
        # process shifts by their total span (see _build_phases).
        cost_start = _epoch(_parse_iso(cost_report.get("start_time")))
        tolerance_s = 1.0
        pre_flags = [
            bool(
                record.get("start_time")
                and cost_start is not None
                and float(record["start_time"]) < cost_start - tolerance_s
            )
            for record in records
        ]
        restored_s = sum(
            float(record.get("duration") or 0.0)
            for record, is_pre in zip(records, pre_flags)
            if is_pre
        )
        live_records = [
            record for record, is_pre in zip(records, pre_flags) if not is_pre
        ]

        t0_epoch, measured = self._resolve_t0(recorder, cost_report, live_records)
        tracker = CostTracker(model=cost_report.get("model") or "claude-5-opus-aws")

        # Origin for in-run coordinates, shifted past the restored span.
        run_origin = t0_epoch - restored_s

        # Phases first (calls need them for containment), with a provisional end
        # that the calls may later extend.
        provisional_end = int(round(
            (float(cost_report.get("duration_seconds") or 0.0) + restored_s) * 1000
        ))
        phases = self._build_phases(records, t0_epoch, provisional_end, pre_flags)

        # Merge calls made by the process that wrote the checkpoint, rebased
        # into the restored phase windows built just above. Without this a
        # resumed run replays those phases as correctly-sized but EMPTY windows
        # -- on 2026-08-24 a --resume-from 3 published a replay with no
        # analyzers and no continuity agent, because the recorder is memory-only
        # and died with the earlier process.
        cost_report, recorder, restored_count = self._merge_restored_calls(
            restored_replay, cost_report, recorder, records, pre_flags, phases, run_origin
        )

        calls = self._build_calls(cost_report, recorder, tracker, run_origin, phases)

        # Pre-run gatherer measurements (resumed run): rebase them into the
        # restored gathering window. Anchor = earliest epoch the gatherers
        # recorded, base = where that window now sits on the merged timeline.
        rebase: Optional[Tuple[float, int]] = None
        if restored_s > 0:
            status_now = orchestrator_result.get("collection_status") or {}
            epochs = []
            for value in status_now.values():
                if not isinstance(value, dict):
                    continue
                for key in ("started_at", "ended_at"):
                    if isinstance(value.get(key), (int, float)):
                        epochs.append(float(value[key]))
                for entry in value.get("steps") or []:
                    if isinstance(entry, dict):
                        for key in ("started_at", "ended_at"):
                            if isinstance(entry.get(key), (int, float)):
                                epochs.append(float(entry[key]))
            if epochs and cost_start is not None and min(epochs) < cost_start - tolerance_s:
                gathering_phase = next(
                    (ph for ph in phases if ph["ordinal"] == "1"), None
                )
                if gathering_phase is not None:
                    rebase = (min(epochs), gathering_phase["start_ms"])

        # The Illustrator has no cost row of its own; fold it in so the hero
        # phase has a visible actor and its result is reachable from the replay.
        hero = self._hero_call(orchestrator_result, phases)
        if hero is not None:
            calls.append(hero)
            calls.sort(key=lambda c: (c["start_ms"], c["id"]))

        duration_ms = max(
            [provisional_end]
            + [c["end_ms"] for c in calls]
            + [p["end_ms"] for p in phases]
            + [int(recorder.get("duration_ms") or 0) if recorder else 0]
        )
        if phases and duration_ms > phases[-1]["end_ms"]:
            phases[-1]["end_ms"] = duration_ms

        sources = self._build_sources(
            orchestrator_result.get("collection_status") or {},
            phases,
            run_origin,
            rebase=rebase,
        )
        agents = self._build_agents(calls, sources)
        concurrency, peak = self._concurrency(calls, duration_ms, CONCURRENCY_INTERVAL_MS)

        stream_blob, truncation = self._build_stream(recorder, calls, date)
        prompt_blob = self._build_prompts(recorder, calls, date)

        tokens = cost_report.get("tokens") or {}
        # The header sums the same spend the replay itemises below it, image
        # generation included; token totals stay LLM-only, because image tokens
        # bill in three classes at three rates and are not comparable.
        total_cost = float((cost_report.get("cost") or {}).get("total") or 0.0)
        total_cost += self._unbilled_image_spend(cost_report, calls)
        status = "success"
        if any(p.get("status") == "failed" for p in phases):
            status = "failed"
        elif any(p.get("status") == "partial" for p in phases):
            status = "partial"

        index = {
            "schema": SCHEMA_VERSION,
            "date": date,
            "coverage_date": orchestrator_result.get("coverage_date") or "",
            "t0": datetime.fromtimestamp(t0_epoch, tz=timezone.utc).isoformat(),
            "duration_ms": duration_ms,
            "generated_by": GENERATED_BY,
            "run": {
                "status": status,
                "total_items_analyzed": int(orchestrator_result.get("total_items_analyzed") or 0),
                "total_cost_usd": round(total_cost, 6),
                "total_input_tokens": int(tokens.get("input_tokens") or 0),
                "total_output_tokens": int(tokens.get("output_tokens") or 0),
                "llm_calls": len(calls),
                "models": sorted({c["model"] for c in calls if c["model"]}),
                "peak_concurrency": peak,
                "stream_available": stream_blob is not None,
                "stream_truncation": truncation,
                # Lets the pane say "not retained for this date" without first
                # firing a 404 for a ~600 KB file that isn't there.
                "prompts_available": prompt_blob is not None,
                # Offline regeneration reconstructs phase boundaries and derives
                # call spans from completion timestamps; surfaced so the UI can
                # say so rather than implying per-event precision it lacks.
                "timings_measured": measured,
                # Calls merged back from a checkpoint on a resumed run. Their
                # spend is excluded from the totals above, which describe what
                # THIS process spent -- so a non-zero value here explains why
                # the call count and the cost do not line up.
                "restored_calls": restored_count,
            },
            "phases": phases,
            "agents": agents,
            "sources": sources,
            "calls": calls,
            "concurrency": concurrency,
        }

        return self._gate_artifacts(index, stream_blob, prompt_blob)

    def _gate_artifacts(
        self,
        index: Dict[str, Any],
        stream_blob: Optional[bytes],
        prompt_blob: Optional[bytes],
    ) -> Tuple[Dict[str, Any], Optional[bytes], Optional[bytes]]:
        """Run the publish gate, degrading rather than discarding where possible.

        A violation in the index is fatal: the index *is* the artifact, and there
        is nothing to publish without it. A violation in the prompts is not --
        the prompts are an extra fold in the UI, so the honest response is to drop
        that one file and publish the rest.

        Losing all three to a prompt-only hit is what turned the 2026-07-31 false
        positive into a missing day instead of a missing fold. Keep the failure
        proportional to what actually failed.
        """
        self._assert_publishable(index)

        # The prompts artifact is the one place a credential could realistically
        # reach the public site: prompts are assembled from config and collected
        # data, so this gate is now doing real work rather than guarding a single
        # static hero prompt. Scan the decompressed text, not the gzip bytes.
        if prompt_blob is not None:
            try:
                self._assert_publishable(
                    json.loads(gzip.decompress(prompt_blob).decode("utf-8")),
                    what="prompt artifact",
                )
            except ValueError as error:
                # Loud, because the alternative reading -- a genuine credential in
                # the prompts -- needs a human. The replay still publishes.
                logger.error("Dropping the replay prompt artifact: %s", error)
                prompt_blob = None

        return index, stream_blob, prompt_blob

    def write(
        self,
        date: str,
        index: Dict[str, Any],
        stream_blob: Optional[bytes],
        prompt_blob: Optional[bytes] = None,
    ) -> str:
        """Write the artifacts into ``web/data/{date}/`` and return the index path."""
        date_dir = os.path.join(self.data_dir, date)
        os.makedirs(date_dir, exist_ok=True)

        index_path = os.path.join(date_dir, "replay-index.json")
        with open(index_path, "w", encoding="utf-8") as handle:
            json.dump(index, handle, ensure_ascii=False, separators=(",", ":"))

        stream_path = os.path.join(date_dir, "replay-stream.json.gz")
        if stream_blob is not None:
            with open(stream_path, "wb") as handle:
                handle.write(stream_blob)
        elif os.path.exists(stream_path):
            # A regeneration that produced no stream must not leave the previous
            # run's deltas behind claiming to describe this one.
            os.remove(stream_path)

        prompt_path = os.path.join(date_dir, "replay-prompts.json.gz")
        if prompt_blob is not None:
            with open(prompt_path, "wb") as handle:
                handle.write(prompt_blob)
        elif os.path.exists(prompt_path):
            os.remove(prompt_path)

        self._prune_prompts(keep_date=date)

        index_kb = os.path.getsize(index_path) / 1024
        stream_kb = len(stream_blob) / 1024 if stream_blob else 0
        prompt_kb = len(prompt_blob) / 1024 if prompt_blob else 0
        logger.info(
            f"Generated replay-index.json ({index_kb:.1f} KB, {len(index['calls'])} calls)"
            + (f" + replay-stream.json.gz ({stream_kb:.1f} KB)" if stream_blob else " (no stream)")
            + (f" + replay-prompts.json.gz ({prompt_kb:.1f} KB)" if prompt_blob else "")
        )
        return index_path

    def _prune_prompts(self, keep_date: str) -> None:
        """Delete prompt artifacts older than the retention window.

        Prompts are ~600 KB/day and every version stays in git history forever, so
        without this the repo gains ~220 MB/year for files almost nobody opens.
        Only the prompts are pruned: the index and stream are what make an old day
        still watchable, and they are an order of magnitude smaller.

        Never raises -- failing to prune is untidy, failing the run is not
        acceptable for a housekeeping step.
        """
        try:
            cutoff = datetime.strptime(keep_date, "%Y-%m-%d") - timedelta(
                days=PROMPT_RETENTION_DAYS
            )
        except ValueError:
            return

        removed = 0
        try:
            for name in os.listdir(self.data_dir):
                if name == keep_date:
                    continue
                try:
                    when = datetime.strptime(name, "%Y-%m-%d")
                except ValueError:
                    continue  # not a date directory
                if when >= cutoff:
                    continue
                stale = os.path.join(self.data_dir, name, "replay-prompts.json.gz")
                if os.path.exists(stale):
                    os.remove(stale)
                    removed += 1
        except OSError as exc:
            logger.warning(f"Could not prune old replay prompts: {exc}")
            return

        if removed:
            logger.info(
                f"Pruned {removed} replay prompt artifact(s) older than "
                f"{PROMPT_RETENTION_DAYS} days"
            )


def generate_replay(
    date: str,
    web_dir: str,
    cost_report: Dict[str, Any],
    orchestrator_result: Dict[str, Any],
    recorder_snapshot: Optional[Dict[str, Any]] = None,
    phase_records: Optional[Sequence[Dict[str, Any]]] = None,
    restored_replay: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Generate the replay artifacts, swallowing any failure.

    The replay is a bonus feature on top of a finished run: losing it must never
    turn a successful pipeline into a failed one.
    """
    try:
        generator = ReplayGenerator(web_dir)
        index, stream_blob, prompt_blob = generator.build(
            date=date,
            cost_report=cost_report,
            orchestrator_result=orchestrator_result,
            recorder_snapshot=recorder_snapshot,
            phase_records=phase_records,
            restored_replay=restored_replay,
        )
        return generator.write(date, index, stream_blob, prompt_blob)
    except Exception as error:  # noqa: BLE001 -- deliberate: never fail the run
        logger.warning(f"Replay generation failed ({type(error).__name__}: {error})", exc_info=True)
        return None


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Regenerate a past day's replay from committed ``data/processed/`` files."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate LLM replay artifacts for a date")
    parser.add_argument("date", help="Report date (YYYY-MM-DD)")
    parser.add_argument("--web-dir", default="web", help="Web output directory (default: web)")
    parser.add_argument(
        "--data-dir", default="data", help="Pipeline data directory (default: data)"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    processed = os.path.join(args.data_dir, "processed")
    cost_path = os.path.join(processed, f"cost_report_{args.date}.json")
    result_path = os.path.join(processed, f"orchestrator_result_{args.date}.json")
    for path in (cost_path, result_path):
        if not os.path.exists(path):
            parser.error(f"missing {path}")

    generator = ReplayGenerator(args.web_dir)
    index, stream_blob, prompt_blob = generator.build(
        date=args.date,
        cost_report=_load_json(cost_path),
        orchestrator_result=_load_json(result_path),
    )
    path = generator.write(args.date, index, stream_blob, prompt_blob)
    print(f"wrote {path} ({os.path.getsize(path) / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

