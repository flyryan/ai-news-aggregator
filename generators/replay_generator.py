"""Builds the LLM replay artifacts from a completed pipeline run.

Two files land in ``web/data/{date}/``:

* ``replay-index.json`` -- small, permanent, and self-sufficient. Everything the
  replay UI needs to draw the newsroom, the Gantt and the funnel.
* ``replay-stream.json.gz`` -- the per-call output deltas that drive the
  typewriter. Heavy, prunable, and capped; the index works without it.

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
from datetime import datetime, timezone
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
}

# Aggregate rows would double-count against their per-platform children.
_SOURCE_SUPERSEDED = {"social"}

# Anything matching these in the finished artifact means something leaked that
# should not be published. Checked before writing, not after.
#
# These target credentials and infrastructure, not URLs in general: model output
# legitimately quotes links (a hero prompt summarising a story about a product
# launch, say), and failing the whole replay over a public https:// in prose
# would be a false positive that loses the day's artifact for no security gain.
# What must never appear is an API key, a bearer token, or the private endpoint
# host -- so those are matched specifically.
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
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
    ):
        self.web_dir = web_dir
        self.data_dir = os.path.join(web_dir, "data")
        self.max_stream_bytes = (
            max_stream_bytes
            if max_stream_bytes is not None
            else _env_int("LLM_REPLAY_MAX_BYTES", DEFAULT_MAX_STREAM_BYTES, minimum=1024)
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
    ) -> List[Dict[str, Any]]:
        """Shape phases, reconstructing boundaries when absolute times are gone.

        ``PhaseTracker.to_dict()`` drops absolute start/end, keeping only
        duration, so a run reloaded from ``orchestrator_result_*.json`` has to
        have its timeline rebuilt by cumulative summation. That assumes phases
        ran back-to-back with no gaps, which is close but not exact -- phase
        containment for calls near a boundary can be off by the elapsed
        non-phase work between them.
        """
        phases: List[Dict[str, Any]] = []
        cursor_ms = 0
        for record in phase_records:
            ordinal, label = _split_phase_name(record.get("name", ""))
            duration = float(record.get("duration") or 0.0)

            if record.get("start_time"):
                start_ms = int(round((float(record["start_time"]) - t0) * 1000))
                end_source = record.get("end_time")
                end_ms = (
                    int(round((float(end_source) - t0) * 1000))
                    if end_source
                    else start_ms + int(round(duration * 1000))
                )
            else:
                start_ms = cursor_ms
                end_ms = start_ms + int(round(duration * 1000))

            start_ms = max(0, start_ms)
            end_ms = max(start_ms, end_ms)
            cursor_ms = end_ms

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

            span: Optional[Dict[str, Any]] = None
            queue = recorded_by_caller.get(caller)
            if queue:
                span = queue.pop(0)

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
                    "attempt": int(context.get("attempt") or 1),
                    "fallback_from": context.get("fallback_from"),
                    "retry_reason": context.get("retry_reason"),
                    "has_stream": has_stream,
                }
            )

        calls.sort(key=lambda c: (c["start_ms"], c["id"]))
        return calls

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
        collection_status: Dict[str, Any], phases: Sequence[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Turn per-source collection results into stage props.

        Gathering has no per-source timing, so every source is stretched across
        the gathering phase. That is honest at the resolution we have: the
        gatherers genuinely do run concurrently for that whole window.
        """
        gathering = next((p for p in phases if p["ordinal"] == "1"), None)
        start_ms = gathering["start_ms"] if gathering else 0
        end_ms = gathering["end_ms"] if gathering else 0

        sources: List[Dict[str, Any]] = []
        for key, value in (collection_status or {}).items():
            if key in _SOURCE_SUPERSEDED:
                continue
            mapping = _SOURCE_LABELS.get(key)
            if not mapping:
                continue
            if not isinstance(value, dict):
                continue
            agent_id, label = mapping
            sources.append(
                {
                    "agent_id": agent_id,
                    "name": label,
                    "items": int(value.get("count") or 0),
                    "status": value.get("status") or "success",
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                }
            )
        return sources

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

        # The image model is not an LLM route, so token/cost/provider fields stay
        # zeroed rather than borrowing plausible-looking numbers from elsewhere.
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
            "model": "gemini-3-pro-image",
            "profile": "STANDARD",
            "effort": "high",
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cost_usd": 0.0,
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
    def _assert_publishable(index: Dict[str, Any]) -> None:
        """Refuse to write anything that carries a credential or private host.

        The artifact is published publicly, so this is the last gate before it
        lands on disk. It deliberately does *not* reject URLs in general -- the
        hero prompt embeds model-written story summaries that may legitimately
        quote a public link, and losing a day's replay over that would be a
        false positive with no security benefit. Credentials, bearer tokens,
        key-value secrets and the private endpoint hosts are what must never
        appear.
        """
        blob = json.dumps(index, ensure_ascii=False)
        for pattern in _SECRET_PATTERNS:
            match = pattern.search(blob)
            if match:
                raise ValueError(
                    f"Replay index contains disallowed content matching {pattern.pattern!r}: "
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
                    f"Replay index leaks the {var} host ({host!r}); refusing to write"
                )

    # -- entry point -----------------------------------------------------

    def build(
        self,
        date: str,
        cost_report: Dict[str, Any],
        orchestrator_result: Dict[str, Any],
        recorder_snapshot: Optional[Dict[str, Any]] = None,
        phase_records: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Tuple[Dict[str, Any], Optional[bytes]]:
        """Shape a run into ``(index, stream_gzip_or_None)``."""
        recorder = recorder_snapshot if (recorder_snapshot or {}).get("calls") else None
        records = list(phase_records or orchestrator_result.get("phase_status") or [])

        t0_epoch, measured = self._resolve_t0(recorder, cost_report, records)
        tracker = CostTracker(model=cost_report.get("model") or "claude-5-opus-aws")

        # Phases first (calls need them for containment), with a provisional end
        # that the calls may later extend.
        provisional_end = int(round(float(cost_report.get("duration_seconds") or 0.0) * 1000))
        phases = self._build_phases(records, t0_epoch, provisional_end)
        calls = self._build_calls(cost_report, recorder, tracker, t0_epoch, phases)

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

        sources = self._build_sources(orchestrator_result.get("collection_status") or {}, phases)
        agents = self._build_agents(calls, sources)
        concurrency, peak = self._concurrency(calls, duration_ms, CONCURRENCY_INTERVAL_MS)

        stream_blob, truncation = self._build_stream(recorder, calls, date)

        tokens = cost_report.get("tokens") or {}
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
                "total_cost_usd": round(float((cost_report.get("cost") or {}).get("total") or 0.0), 6),
                "total_input_tokens": int(tokens.get("input_tokens") or 0),
                "total_output_tokens": int(tokens.get("output_tokens") or 0),
                "llm_calls": len(calls),
                "models": sorted({c["model"] for c in calls if c["model"]}),
                "peak_concurrency": peak,
                "stream_available": stream_blob is not None,
                "stream_truncation": truncation,
                # Offline regeneration reconstructs phase boundaries and derives
                # call spans from completion timestamps; surfaced so the UI can
                # say so rather than implying per-event precision it lacks.
                "timings_measured": measured,
            },
            "phases": phases,
            "agents": agents,
            "sources": sources,
            "calls": calls,
            "concurrency": concurrency,
        }

        self._assert_publishable(index)
        return index, stream_blob

    def write(self, date: str, index: Dict[str, Any], stream_blob: Optional[bytes]) -> str:
        """Write both artifacts into ``web/data/{date}/`` and return the index path."""
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

        index_kb = os.path.getsize(index_path) / 1024
        stream_kb = len(stream_blob) / 1024 if stream_blob else 0
        logger.info(
            f"Generated replay-index.json ({index_kb:.1f} KB, {len(index['calls'])} calls)"
            + (f" + replay-stream.json.gz ({stream_kb:.1f} KB)" if stream_blob else " (no stream)")
        )
        return index_path


def generate_replay(
    date: str,
    web_dir: str,
    cost_report: Dict[str, Any],
    orchestrator_result: Dict[str, Any],
    recorder_snapshot: Optional[Dict[str, Any]] = None,
    phase_records: Optional[Sequence[Dict[str, Any]]] = None,
) -> Optional[str]:
    """Generate the replay artifacts, swallowing any failure.

    The replay is a bonus feature on top of a finished run: losing it must never
    turn a successful pipeline into a failed one.
    """
    try:
        generator = ReplayGenerator(web_dir)
        index, stream_blob = generator.build(
            date=date,
            cost_report=cost_report,
            orchestrator_result=orchestrator_result,
            recorder_snapshot=recorder_snapshot,
            phase_records=phase_records,
        )
        return generator.write(date, index, stream_blob)
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
    index, stream_blob = generator.build(
        date=args.date,
        cost_report=_load_json(cost_path),
        orchestrator_result=_load_json(result_path),
    )
    path = generator.write(args.date, index, stream_blob)
    print(f"wrote {path} ({os.path.getsize(path) / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

