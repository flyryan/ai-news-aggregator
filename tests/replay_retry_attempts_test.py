"""Retried LLM calls must survive into the replay as separate attempts.

Regression test for a merge bug found on 2026-07-28. Cost rows are the spine of
``_build_calls``, and only a *successful* response produces one. Spans were paired
to rows positionally per caller, so when a batch failed and was retried:

  * the failed span was handed to the retry's cost row -- the call rendered
    "failed" while carrying the successful attempt's tokens, duration and cost;
  * the surviving span was dropped on the floor.

Five research/reddit batches showed as failed in the published replay for a run
whose own logs recorded ``5/5`` and ``7/7`` batches successful. Nothing was
actually lost by the pipeline; the replay was lying about it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.cost_tracker import CostTracker
from generators.replay_generator import ReplayGenerator


T0 = 1_000_000.0


def _span(call_id, caller, queued_ms, end_ms, outcome, text_chars=0, **ctx):
    return {
        "id": call_id,
        "caller": caller,
        "queued_ms": queued_ms,
        "start_ms": queued_ms,
        "end_ms": end_ms,
        "first_token_ms": queued_ms + 100,
        "wait_ms": 0,
        "outcome": outcome,
        "stop_reason": None if outcome == "failed" else "end_turn",
        "text_chars": text_chars,
        "thinking_chars": 0,
        "delta_events": 10 if text_chars else 0,
        "context": {"provider_id": "gcp", "attempt": 1, **ctx},
        "deltas": {"t": [1], "kind": [1], "text": ["x"]} if text_chars else {},
    }


def _cost_row(caller, output_tokens, duration_s, timestamp_offset_s, partial=False):
    from datetime import datetime, timezone

    return {
        "caller": caller,
        "timestamp": datetime.fromtimestamp(
            T0 + timestamp_offset_s, tz=timezone.utc
        ).isoformat(),
        "input_tokens": 1000,
        "output_tokens": output_tokens,
        "duration_seconds": duration_s,
        "model": "claude-5-opus-gcp",
        "provider_id": "gcp",
        "analysis_profile": "STANDARD",
        "adaptive_effort": "xhigh",
        "partial": partial,
    }


def _build(spans, rows):
    gen = ReplayGenerator(web_dir="/tmp/replay-test-unused")
    phases = [{"id": "phase-2", "ordinal": "2", "label": "Analysis",
               "start_ms": 0, "end_ms": 600_000, "status": "success"}]
    return gen._build_calls(
        cost_report={"calls": rows},
        recorder={"calls": spans},
        tracker=CostTracker(),
        t0=T0,
        phases=phases,
    )


def test_failed_attempt_and_its_retry_are_both_kept():
    """One failure + one success => two calls, each labelled correctly."""
    caller = "research_analyzer.batch_3"
    spans = [
        _span("c001", caller, 10_000, 205_000, "failed", text_chars=35_656),
        _span("c002", caller, 210_000, 546_000, "ok", text_chars=40_000),
    ]
    rows = [_cost_row(caller, output_tokens=27_407, duration_s=336.9, timestamp_offset_s=546)]

    calls = _build(spans, rows)
    assert len(calls) == 2, f"expected both attempts, got {len(calls)}"

    failed = [c for c in calls if c["outcome"] == "failed"]
    ok = [c for c in calls if c["outcome"] == "ok"]
    assert len(failed) == 1 and len(ok) == 1

    # The success must carry the cost row's numbers, not the failure's timings.
    assert ok[0]["output_tokens"] == 27_407
    assert ok[0]["cost_usd"] > 0
    assert ok[0]["end_ms"] == 546_000

    # The failure must NOT claim the successful call's tokens or cost.
    assert failed[0]["output_tokens"] == 0
    assert failed[0]["cost_usd"] == 0.0
    # ...and must be explicit that this is unknown spend, not free.
    assert failed[0]["billed"] is False


def test_failure_is_linked_to_the_attempt_that_recovered_it():
    caller = "reddit_analyzer.batch_6"
    spans = [
        _span("c010", caller, 5_000, 60_000, "failed", text_chars=21_093),
        _span("c011", caller, 61_000, 200_000, "ok", text_chars=30_000),
    ]
    rows = [_cost_row(caller, output_tokens=12_000, duration_s=139.0, timestamp_offset_s=200)]

    calls = _build(spans, rows)
    failed = next(c for c in calls if c["outcome"] == "failed")
    ok = next(c for c in calls if c["outcome"] == "ok")

    assert failed["recovered_by"] == ok["id"]
    assert ok["recovers"] == failed["id"]


def test_unrecovered_failure_stays_unlinked():
    """A failure with no later success really did lose its batch -- don't imply otherwise."""
    caller = "news_analyzer.batch_0"
    spans = [_span("c020", caller, 5_000, 60_000, "failed", text_chars=900)]
    calls = _build(spans, rows=[])

    assert len(calls) == 1
    assert calls[0]["outcome"] == "failed"
    assert "recovered_by" not in calls[0]


def test_clean_run_is_unchanged():
    """No failures => one call per cost row, fully populated, no extra rows."""
    spans = [
        _span("c030", "news_analyzer.batch_0", 1_000, 100_000, "ok", text_chars=5_000),
        _span("c031", "news_analyzer.batch_1", 2_000, 120_000, "ok", text_chars=6_000),
    ]
    rows = [
        _cost_row("news_analyzer.batch_0", 9_000, 99.0, 100),
        _cost_row("news_analyzer.batch_1", 9_500, 118.0, 120),
    ]

    calls = _build(spans, rows)
    assert len(calls) == 2
    assert all(c["outcome"] == "ok" for c in calls)
    assert all(c["cost_usd"] > 0 for c in calls)
    assert all("recovered_by" not in c for c in calls)


def test_partial_row_pairs_with_the_failed_span_not_the_success():
    """A mid-stream failure now bills what it streamed; the money must land right.

    Both attempts have a cost row: the failure's is flagged `partial` (tokens read
    off the SSE events). Pairing must route each row to a span of matching
    disposition, or the failure's spend gets attached to the successful call.
    """
    caller = "research_analyzer.batch_3"
    spans = [
        _span("c050", caller, 10_000, 205_000, "failed", text_chars=35_656),
        _span("c051", caller, 210_000, 546_000, "ok", text_chars=40_000),
    ]
    rows = [
        _cost_row(caller, output_tokens=18_000, duration_s=195.0,
                  timestamp_offset_s=205, partial=True),
        _cost_row(caller, output_tokens=27_407, duration_s=336.9, timestamp_offset_s=546),
    ]

    calls = _build(spans, rows)
    assert len(calls) == 2

    failed = next(c for c in calls if c["outcome"] == "failed")
    ok = next(c for c in calls if c["outcome"] == "ok")

    # The failure keeps the partial figures...
    assert failed["output_tokens"] == 18_000
    assert failed["cost_usd"] > 0, "a mid-stream failure was still billed"
    assert failed["billed_exact"] is False
    # ...and the success keeps its own, exactly.
    assert ok["output_tokens"] == 27_407
    assert ok["billed_exact"] is True
    # The link between them survives.
    assert failed["recovered_by"] == ok["id"]


def test_concurrent_callers_do_not_swap_spans():
    """Distinct callers must never borrow each other's timings."""
    spans = [
        _span("c040", "research_analyzer.batch_0", 1_000, 300_000, "ok", text_chars=1),
        _span("c041", "reddit_analyzer.batch_0", 1_500, 90_000, "ok", text_chars=1),
    ]
    rows = [
        _cost_row("reddit_analyzer.batch_0", 5_000, 88.5, 90),
        _cost_row("research_analyzer.batch_0", 25_000, 299.0, 300),
    ]

    calls = _build(spans, rows)
    by_caller = {c["caller"]: c for c in calls}
    assert by_caller["research_analyzer.batch_0"]["end_ms"] == 300_000
    assert by_caller["reddit_analyzer.batch_0"]["end_ms"] == 90_000
