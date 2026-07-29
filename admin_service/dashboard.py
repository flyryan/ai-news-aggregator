"""Assemble dashboard data from committed report files.

Everything here reads what the pipeline already publishes into web/data. No
GitHub API, no network: this is the tier that works offline, has full history,
and costs nothing.

The one judgement encoded here: a date with no published report yields None,
not 0. Zero means "collected nothing", which is an outage; None means "no
report", which is a gap. Conflating them turns every missed day into a false
alarm.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

__all__ = ["health_series", "cost_series", "latest_report"]

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_detector():
    """Load the detector by path, bypassing the `agents` package __init__.

    `agents/__init__.py` imports llm_client -> httpx -> the whole pipeline
    dependency tree. The admin service should not inherit that just to compute
    a median; the detector itself is stdlib-only by design.
    """
    if "source_anomaly" in sys.modules:
        return sys.modules["source_anomaly"]
    spec = importlib.util.spec_from_file_location(
        "source_anomaly", _REPO_ROOT / "agents" / "source_anomaly.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # @dataclass needs this before exec
    spec.loader.exec_module(module)
    return module


def _published_dates(web_dir: Path) -> list[str]:
    data_dir = Path(web_dir) / "data"
    if not data_dir.is_dir():
        return []
    return sorted(p.parent.name for p in data_dir.glob("*/summary.json"))


def health_series(web_dir: Path, days: int = 90) -> dict[str, Any]:
    """Per-source item counts over a trailing window, with anomaly flags."""
    detector = _load_detector()
    web_dir = Path(web_dir)
    readings = detector.load_history(web_dir)
    if not readings:
        return {"sources": [], "dates": [], "series": {}, "anomalies": []}

    # Detect over ALL history so baselines are well-formed, then window the view.
    anomalies = detector.detect(readings)

    dates = sorted({r.date for r in readings})[-days:]
    date_set = set(dates)

    by_source: dict[str, dict[str, int]] = {}
    for reading in readings:
        by_source.setdefault(reading.source, {})[reading.date] = reading.count

    # Order sources by typical volume so the heaviest lanes read first.
    sources = sorted(by_source, key=lambda s: -max(by_source[s].values(), default=0))

    series = {
        source: [by_source[source].get(date) for date in dates]
        for source in sources
    }

    return {
        "sources": sources,
        "dates": dates,
        "series": series,
        "anomalies": [
            {
                "date": a.date,
                "source": a.source,
                "count": a.count,
                "baseline": round(a.baseline),
                "weekday": a.weekday,
                "ratio": round(a.ratio, 3),
                "detail": a.describe(),
            }
            for a in anomalies
            if a.date in date_set
        ],
    }


def cost_series(web_dir: Path, days: int = 90) -> list[dict[str, Any]]:
    """Per-run cost and token totals from committed replay indexes.

    replay-index.json already carries everything the cost panel needs, so no
    artifact download or ingest is required. Days without a replay index are
    simply absent -- the feature is new and most of the archive predates it.
    """
    web_dir = Path(web_dir)
    rows: list[dict[str, Any]] = []

    for date in _published_dates(web_dir)[-days:]:
        index_path = web_dir / "data" / date / "replay-index.json"
        if not index_path.is_file():
            continue
        try:
            payload = json.loads(index_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        run = payload.get("run") or {}
        rows.append({
            "date": date,
            "cost_usd": float(run.get("total_cost_usd") or 0.0),
            "llm_calls": int(run.get("llm_calls") or 0),
            "input_tokens": int(run.get("total_input_tokens") or 0),
            "output_tokens": int(run.get("total_output_tokens") or 0),
            "items": int(run.get("total_items_analyzed") or 0),
            "status": str(run.get("status") or "unknown"),
            "duration_ms": int(payload.get("duration_ms") or 0),
            # False means the run was reconstructed offline; wait_ms and
            # first_token_ms are unrecoverable after the fact. The UI must not
            # present a reconstruction as a measurement.
            "timings_measured": bool(run.get("timings_measured", False)),
        })

    return rows


def latest_report(web_dir: Path) -> dict[str, Any] | None:
    """Headline numbers for the most recent published day."""
    web_dir = Path(web_dir)
    dates = _published_dates(web_dir)
    if not dates:
        return None

    date = dates[-1]
    try:
        index = json.loads((web_dir / "data" / "index.json").read_text())
    except (OSError, json.JSONDecodeError):
        index = {}

    entry = next((d for d in index.get("dates", []) if d.get("date") == date), {})

    try:
        summary = json.loads((web_dir / "data" / date / "summary.json").read_text())
    except (OSError, json.JSONDecodeError):
        summary = {}

    return {
        "date": date,
        "total_items": entry.get("total_items", 0),
        "categories": entry.get("categories", {}),
        "topics": len(summary.get("top_topics") or []),
        "generated_at": summary.get("generated_at"),
        "has_replay": (web_dir / "data" / date / "replay-index.json").is_file(),
    }
