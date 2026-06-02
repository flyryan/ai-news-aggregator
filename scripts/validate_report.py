#!/usr/bin/env python3
"""
Validate a generated/published daily report's summary.json.

Purpose
-------
The daily pipeline can exit 0 ("PIPELINE COMPLETED SUCCESSFULLY") even when the
Executive Summary and Topic Detection phases failed, because those failures are
caught internally and written into summary.json as sentinel strings rather than
raised. On 2026-06-02 a ~90 min triple-provider outage produced a published
report whose executive_summary literally read "Executive summary generation
failed: Connection error." while CI stayed green.

This script inspects the actual user-facing artifact (summary.json) and exits
non-zero when the report is not publishable, so callers can gate a commit
(publish gate) or trigger a re-run (watchdog).

Usage
-----
  # Validate a freshly generated local report (publish gate, in CI):
  python3 scripts/validate_report.py --web-dir ./web --date 2026-06-02

  # Validate the live published report (watchdog):
  python3 scripts/validate_report.py --url https://news.aatf.ai --date 2026-06-02

Exit codes
----------
  0  report is valid / publishable
  1  report is INVALID (failed checks) -> caller should block or re-run
  2  could not load the report at all (missing file / fetch error / bad JSON)

When --date is omitted it defaults to "today" in America/New_York.
Add --json to emit a machine-readable result object on stdout.
"""

import argparse
import json
import sys
import urllib.request
import urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

# Substrings that indicate a phase wrote a failure sentinel instead of real content.
# Matched case-insensitively against the executive summary text.
FAILURE_SENTINELS = (
    "executive summary generation failed",
    "generation failed:",
    "connection error",
    "apiconnectionerror",
    "apitimeouterror",
    "error:",
)

# Minimum acceptable executive summary length (chars). The failure sentinel is
# ~54 chars; a real summary on this platform runs 1.5k-5k chars. 400 is a
# conservative floor that flags truncated/empty output without false-flagging a
# genuinely short day.
MIN_EXEC_SUMMARY_CHARS = 400


def _load_local(web_dir: str, date_str: str) -> dict:
    import os
    path = os.path.join(web_dir, "data", date_str, "summary.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"summary.json not found at {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_url(base_url: str, date_str: str) -> dict:
    url = base_url.rstrip("/") + f"/data/{date_str}/summary.json"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ai-news-report-validator/1.0", "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise IOError(f"HTTP {resp.status} fetching {url}")
        return json.loads(resp.read().decode("utf-8"))


def validate(summary: dict, date_str: str) -> dict:
    """Return a result dict: {valid: bool, failures: [str], warnings: [str], stats: {}}."""
    failures = []
    warnings = []

    exec_summary = (summary.get("executive_summary") or "").strip()
    exec_lower = exec_summary.lower()
    top_topics = summary.get("top_topics") or []
    analyzed = summary.get("total_items_analyzed") or 0
    collected = summary.get("total_items_collected") or 0
    report_date = summary.get("date") or summary.get("coverage_date") or ""

    # 1) Executive summary must be non-empty and substantive.
    if not exec_summary:
        failures.append("executive_summary is empty")
    elif len(exec_summary) < MIN_EXEC_SUMMARY_CHARS:
        failures.append(
            f"executive_summary too short ({len(exec_summary)} < {MIN_EXEC_SUMMARY_CHARS} chars)"
        )

    # 2) Executive summary must not be a failure sentinel.
    for sentinel in FAILURE_SENTINELS:
        if sentinel in exec_lower:
            failures.append(f"executive_summary contains failure sentinel: {sentinel!r}")
            break

    # 3) Topic detection must have produced topics.
    if not isinstance(top_topics, list) or len(top_topics) == 0:
        failures.append("top_topics is empty (topic detection failed)")

    # 4) Analysis must have produced items (catches a gather/analysis wipeout).
    if analyzed <= 0:
        failures.append(f"total_items_analyzed is {analyzed} (no analyzed items)")

    # 5) Date sanity: published report should match the requested date.
    if report_date and report_date != date_str:
        warnings.append(f"report date {report_date!r} != requested {date_str!r}")

    # Non-fatal quality signals.
    if not summary.get("hero_image_url"):
        warnings.append("hero_image_url missing (hero fallback or failure)")

    return {
        "valid": len(failures) == 0,
        "failures": failures,
        "warnings": warnings,
        "stats": {
            "date": report_date,
            "exec_summary_chars": len(exec_summary),
            "top_topics": len(top_topics) if isinstance(top_topics, list) else 0,
            "total_items_collected": collected,
            "total_items_analyzed": analyzed,
            "hero_image_url": summary.get("hero_image_url"),
        },
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Validate a daily report's summary.json")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--web-dir", help="Local web dir containing data/<date>/summary.json")
    src.add_argument("--url", help="Base URL of the published site, e.g. https://news.aatf.ai")
    p.add_argument("--date", help="Report date YYYY-MM-DD (default: today in America/New_York)")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON result")
    args = p.parse_args()

    if not args.web_dir and not args.url:
        args.web_dir = "./web"

    date_str = args.date or datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")

    # Load the report; load failure is its own exit code (2) so callers can tell
    # "couldn't check" apart from "checked and it's bad".
    try:
        if args.url:
            summary = _load_url(args.url, date_str)
            source = args.url.rstrip("/") + f"/data/{date_str}/summary.json"
        else:
            summary = _load_local(args.web_dir, date_str)
            source = f"{args.web_dir}/data/{date_str}/summary.json"
    except (FileNotFoundError, urllib.error.URLError, urllib.error.HTTPError, IOError, json.JSONDecodeError) as e:
        result = {"valid": False, "loaded": False, "error": str(e), "date": date_str}
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"LOAD ERROR: could not load report for {date_str}: {e}", file=sys.stderr)
        return 2

    result = validate(summary, date_str)
    result["loaded"] = True
    result["source"] = source

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        stats = result["stats"]
        status = "VALID" if result["valid"] else "INVALID"
        print(f"[{status}] report {date_str} ({source})")
        print(
            f"  exec_summary={stats['exec_summary_chars']} chars | "
            f"topics={stats['top_topics']} | "
            f"analyzed={stats['total_items_analyzed']} | "
            f"collected={stats['total_items_collected']}"
        )
        for f in result["failures"]:
            print(f"  FAIL: {f}")
        for w in result["warnings"]:
            print(f"  warn: {w}")

    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
