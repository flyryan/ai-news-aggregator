#!/usr/bin/env python3
"""Post-run check: did any source silently stop producing?

Runs after the pipeline publishes. Compares each source's item count for the
report date against its same-weekday baseline and, with --alert, notifies the
existing pipeline alert ingress with status "degraded".

The existing alert fires only when the JOB fails. This one fires when the job
SUCCEEDS but the data is wrong -- which is how a three-week arXiv outage went
unnoticed while every run stayed green.

  python3 scripts/check_source_anomalies.py --web-dir web --date 2026-06-22
  python3 scripts/check_source_anomalies.py --alert          # in CI

Exit: 0 clean | 1 anomalies found | 2 usage or IO error
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_ALERT_URL = "https://flybotwebhook.duffplex.com/alert/pipeline"


def _load_detector():
    """Load the detector by path, bypassing the `agents` package __init__.

    `agents/__init__.py` imports llm_client -> httpx. This script runs in CI
    right after the pipeline, where httpx is present, but it must also run on
    the admin host and from a bare checkout. The detector is stdlib-only by
    design; keep it that way at the call site too.
    """
    spec = importlib.util.spec_from_file_location(
        "source_anomaly", REPO_ROOT / "agents" / "source_anomaly.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # @dataclass needs this before exec
    spec.loader.exec_module(module)
    return module


_detector = _load_detector()
detect_for_date = _detector.detect_for_date
load_history = _detector.load_history


def _today_et() -> str:
    """Today in America/New_York without pulling in a tz dependency.

    ET is UTC-5 (EST) or UTC-4 (EDT). Using -5 year-round can only shift the
    date backward by an hour's worth of edge case near midnight, and CI always
    passes --date explicitly.
    """
    return (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d")


def _post_alert(anomalies, report_date: str, run_url: str) -> None:
    """Best-effort POST to the shared alert ingress. Never raises."""
    token = os.environ.get("PIPELINE_ALERT_TOKEN", "").strip()
    if not token:
        print("PIPELINE_ALERT_TOKEN not set; skipping alert POST.")
        return

    url = os.environ.get("PIPELINE_ALERT_URL", "").strip() or DEFAULT_ALERT_URL
    payload = {
        "status": "degraded",
        "report_date": report_date,
        "reason": (
            f"{len(anomalies)} source(s) collected far below their same-weekday "
            "baseline; the run itself succeeded"
        ),
        "run_url": run_url,
        "anomalies": [
            {
                "source": a.source,
                "count": a.count,
                "baseline": round(a.baseline),
                "weekday": a.weekday,
                "ratio": round(a.ratio, 3),
                "detail": a.describe(),
            }
            for a in anomalies
        ],
    }

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            print(f"Alert POST -> HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        print(f"Alert POST -> HTTP {exc.code} (delivery failed, not failing the run)")
    except Exception as exc:  # noqa: BLE001 - delivery must never break the caller
        print(f"Alert POST failed: {type(exc).__name__} (not failing the run)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check a published report for sources that collapsed against baseline"
    )
    parser.add_argument("--web-dir", default="web", help="Directory containing data/<date>/")
    parser.add_argument("--date", help="Report date YYYY-MM-DD (default: today in ET)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument(
        "--alert",
        action="store_true",
        help="POST a degraded alert when anomalies are found",
    )
    args = parser.parse_args()

    web_dir = Path(args.web_dir)
    if not (web_dir / "data").is_dir():
        print(f"error: {web_dir}/data is not a directory", file=sys.stderr)
        return 2

    report_date = args.date or _today_et()

    try:
        readings = load_history(web_dir)
    except OSError as exc:
        print(f"error: could not read history: {exc}", file=sys.stderr)
        return 2

    if not any(r.date == report_date for r in readings):
        print(f"No published data for {report_date}; nothing to check.")
        return 0

    anomalies = detect_for_date(readings, report_date)

    if args.json:
        print(json.dumps(
            {
                "date": report_date,
                "anomaly_count": len(anomalies),
                "anomalies": [
                    {
                        "source": a.source, "count": a.count,
                        "baseline": round(a.baseline), "weekday": a.weekday,
                        "ratio": round(a.ratio, 3),
                    }
                    for a in anomalies
                ],
            },
            indent=2,
        ))
    elif anomalies:
        print(f"DEGRADED: {len(anomalies)} source(s) below baseline on {report_date}")
        for anomaly in anomalies:
            print(f"  - {anomaly.describe()}")
    else:
        print(f"OK: all sources within their same-weekday baselines on {report_date}")

    if anomalies and args.alert:
        _post_alert(anomalies, report_date, os.environ.get("RUN_URL", ""))

    return 1 if anomalies else 0


if __name__ == "__main__":
    raise SystemExit(main())
