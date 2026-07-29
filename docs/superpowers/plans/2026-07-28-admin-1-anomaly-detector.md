# Source Anomaly Detector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect a source that has silently stopped producing items, using a day-of-week-aware baseline over committed report history, and alert once per incident through the existing pipeline alert channel.

**Architecture:** One pure-Python module, `agents/source_anomaly.py`, with no dependencies outside the standard library. It reads the `collection_status` block already committed in `web/data/<date>/summary.json` for every published day and compares each source's item count against the trailing median of the *same report weekday*. Two callers import it: the daily pipeline (post-run check, emits a `degraded` alert) and, later, the admin dashboard (timeline view). One module means "anomalous" has exactly one definition and cannot drift between them.

**Tech Stack:** Python 3.11+ stdlib only (`json`, `statistics`, `datetime`, `pathlib`, `argparse`, `urllib`), stdlib `unittest`, GitHub Actions, `curl`.

## Global Constraints

- **Stdlib only** in `agents/source_anomaly.py`. It runs in the dependency-light CI guard job and, later, inside the admin service; adding a dependency to either is a cost with no benefit here.
- Tests are stdlib `unittest`, named `tests/<name>_test.py`, run as `python3 -m unittest tests.<module> -v` (`.github/workflows/tests.yml:34-38`).
- The alert endpoint, token, and payload shape are **fixed by the existing implementation** (`.github/workflows/daily-pipeline.yml:500-531`): POST to `PIPELINE_ALERT_URL` (default `https://flybotwebhook.duffplex.com/alert/pipeline`) with `Authorization: Bearer ${PIPELINE_ALERT_TOKEN}`, `Content-Type: application/json`. Alert delivery must **never** fail the job.
- Alerting must be **best-effort and fail-closed on config**: if `PIPELINE_ALERT_TOKEN` is unset, skip the POST and exit 0 (mirrors `daily-pipeline.yml:509-512`).
- Baselines key on the **report date's weekday**, never the coverage date's. This mirrors `agents/gatherers/research_gatherer.py:148-153`, where Sat/Sun reports skip arXiv entirely and Monday runs a 3-day Sat–Mon catch-up.
- Do **not** read `collection_status.status` or `.overall` as a health signal. They read `"success"` on all 215 published days because failed runs never publish (`daily-pipeline.yml:373-384` reverts generated data on failure). Use item counts.
- Commits must be SSH-signed.

---

## Background the implementer needs

This detector exists because of a specific incident. On three consecutive Monday reports — 2026-06-22, 06-29, and 07-06 — arXiv returned zero papers and the research category collected 8, 18, and 16 items against a 268–782 norm for that weekday. The few items that landed were LessWrong. Every one of those days published with `collection_status.overall: "success"`. The regression ran for three weeks until commit `a281939` fixed it.

Two design consequences follow, and both are load-bearing:

1. **Status fields are useless here; counts are the signal.** Published data is survivorship-biased — only successful runs are ever committed.
2. **The baseline must be weekday-aware.** Research volume by report weekday is roughly Sat 19 / Sun 11 / Mon 374 / Tue 996 / Wed 530 / Thu 492 / Fri 531. A flat threshold fires **35 times in 215 days**, nearly all false positives from weekend arXiv skips. The weekday-aware version produces **5 anomaly rows across 1,505 readings — 0.70/month**: the three arXiv Mondays, plus the 2026-04-10 social collapse counted once as the `social` category (172 against a 593 median) and once as the `twitter` platform that caused it (165 against 580). Those numbers were measured against the real history and are asserted in the tests below.

Counting a single incident at both category and platform granularity is intentional. The category row tells you something is wrong; the platform row tells you which upstream broke. Dropping the narrower row to make the total tidier would discard the more actionable of the two.

---

## File Structure

| File | Responsibility |
|---|---|
| `agents/source_anomaly.py` (create) | The detector. Loads history, computes weekday baselines, yields anomalies. Pure functions plus a small CLI. |
| `tests/source_anomaly_test.py` (create) | Unit tests on synthetic data, plus a regression test against the real committed history asserting exactly the five known anomaly rows. |
| `scripts/check_source_anomalies.py` (create) | CI entry point: run the detector for one date, print a report, optionally POST a `degraded` alert. |
| `.github/workflows/daily-pipeline.yml` (modify, after `:470`) | Invoke the checker after the publish assertion. |
| `.github/workflows/tests.yml` (modify) | Run the detector tests in the guard job. |

---

### Task 1: The detector module

**Files:**
- Create: `agents/source_anomaly.py`
- Test: `tests/source_anomaly_test.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `@dataclass(frozen=True) SourceReading(date: str, source: str, count: int)`
  - `@dataclass(frozen=True) Anomaly(date: str, source: str, count: int, baseline: float, weekday: str, ratio: float)` with `.describe() -> str`
  - `load_history(web_dir: Path) -> list[SourceReading]`
  - `report_weekday(date: str) -> int` (0=Mon … 6=Sun)
  - `detect(readings, *, window=6, min_baseline=25, ratio_threshold=0.35) -> list[Anomaly]`
  - `detect_for_date(readings, target_date, **kw) -> list[Anomaly]`
  - Module constants `DEFAULT_WINDOW = 6`, `DEFAULT_MIN_BASELINE = 25`, `DEFAULT_RATIO = 0.35`

- [ ] **Step 1: Write the failing test**

Create `tests/source_anomaly_test.py`:

```python
"""Tests for the day-of-week-aware source anomaly detector.

Why this detector exists
------------------------
arXiv silently returned zero papers on three consecutive Monday reports
(2026-06-22, 06-29, 07-06): research collected 8, 18, and 16 items against a
268-782 norm, and every one of those days published with
`collection_status.overall: "success"`. Failed runs never publish -- the
workflow reverts generated data on failure -- so committed history is
survivorship-biased and the status field is always green. Item counts are the
only real signal.

Why weekday-aware
-----------------
`agents/gatherers/research_gatherer.py:148-153` skips arXiv entirely on
Saturday/Sunday reports and runs a 3-day Sat-Mon catch-up on Mondays. Research
medians by report weekday are roughly Sat 19 / Sun 11 / Mon 374 / Tue 996. A
flat threshold treats every weekend as an outage: measured against the real
215-day history it fires 35 times, nearly all false. Keying the baseline to the
same report weekday yields 5 rows across 1,505 readings, and all 5 are real.

Stdlib-only, matching the repo's other tests:

  python3 -m unittest tests.source_anomaly_test -v
"""

import unittest
from pathlib import Path

from agents.source_anomaly import (
    Anomaly,
    SourceReading,
    detect,
    detect_for_date,
    load_history,
    report_weekday,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "web"

# The five anomalies the detector must find in the real committed history, and
# nothing else. Three are the arXiv outage. The other two are one incident seen
# at two granularities: the 2026-04-10 social collapse (172 against a 593
# same-weekday median) and the Twitter platform that caused it (165 against
# 580). Both are kept deliberately -- the category row says something is wrong,
# the platform row says which one, and suppressing the narrower signal to make
# the count prettier would throw away the more actionable half.
KNOWN_INCIDENTS = {
    ("2026-04-10", "social"),
    ("2026-04-10", "twitter"),
    ("2026-06-22", "research"),
    ("2026-06-29", "research"),
    ("2026-07-06", "research"),
}


def _steady(source, dates, count):
    return [SourceReading(d, source, count) for d in dates]


class ReportWeekdayTest(unittest.TestCase):
    def test_maps_iso_date_to_weekday_index(self):
        # 2026-07-27 is a Monday; 2026-07-25 a Saturday.
        self.assertEqual(0, report_weekday("2026-07-27"))
        self.assertEqual(5, report_weekday("2026-07-25"))

    def test_uses_the_report_date_not_the_coverage_date(self):
        # The coverage day is the day before, but the gatherer's skip/catch-up
        # logic branches on the REPORT date. Keying on coverage would shift every
        # baseline by one day and make Monday catch-ups look like Sundays.
        self.assertEqual(0, report_weekday("2026-06-22"))  # Monday report


class DetectTest(unittest.TestCase):
    def test_flags_a_collapse_against_the_same_weekday_baseline(self):
        # Eight Mondays at 400, then one at 10.
        mondays = [
            "2026-05-04", "2026-05-11", "2026-05-18", "2026-05-25",
            "2026-06-01", "2026-06-08", "2026-06-15",
        ]
        readings = _steady("research", mondays, 400)
        readings.append(SourceReading("2026-06-22", "research", 10))

        found = detect(readings)

        self.assertEqual(1, len(found), f"expected exactly one anomaly, got {found}")
        self.assertEqual("2026-06-22", found[0].date)
        self.assertEqual("research", found[0].source)
        self.assertEqual(10, found[0].count)
        self.assertAlmostEqual(400.0, found[0].baseline)

    def test_does_not_flag_a_low_but_normal_weekend(self):
        # Saturdays legitimately collect ~15 (arXiv skipped) while weekdays
        # collect ~500. A weekday-blind detector flags every Saturday.
        readings = []
        weekdays = ["2026-05-05", "2026-05-06", "2026-05-07", "2026-05-12",
                    "2026-05-13", "2026-05-14", "2026-05-19", "2026-05-20"]
        saturdays = ["2026-05-02", "2026-05-09", "2026-05-16", "2026-05-23",
                     "2026-05-30", "2026-06-06"]
        readings += _steady("research", weekdays, 500)
        readings += _steady("research", saturdays, 15)

        self.assertEqual([], detect(readings))

    def test_ignores_sources_whose_baseline_is_too_small_to_judge(self):
        # A source that normally returns 3 items dropping to 0 is noise, not signal.
        dates = ["2026-05-04", "2026-05-11", "2026-05-18", "2026-05-25",
                 "2026-06-01", "2026-06-08"]
        readings = _steady("tiny", dates, 3)
        readings.append(SourceReading("2026-06-15", "tiny", 0))

        self.assertEqual([], detect(readings))

    def test_needs_enough_history_before_judging(self):
        # With only two prior samples there is no trustworthy baseline yet.
        readings = [
            SourceReading("2026-06-08", "research", 400),
            SourceReading("2026-06-15", "research", 400),
            SourceReading("2026-06-22", "research", 5),
        ]
        self.assertEqual([], detect(readings))

    def test_ratio_threshold_is_a_boundary_not_a_cliff(self):
        dates = ["2026-05-04", "2026-05-11", "2026-05-18", "2026-05-25",
                 "2026-06-01", "2026-06-08"]
        # 40% of baseline survives; 30% does not.
        ok = _steady("research", dates, 100) + [SourceReading("2026-06-15", "research", 40)]
        bad = _steady("research", dates, 100) + [SourceReading("2026-06-15", "research", 30)]

        self.assertEqual([], detect(ok))
        self.assertEqual(1, len(detect(bad)))

    def test_detect_for_date_scopes_to_one_day(self):
        mondays = ["2026-05-04", "2026-05-11", "2026-05-18", "2026-05-25",
                   "2026-06-01", "2026-06-08"]
        readings = _steady("research", mondays, 400)
        readings.append(SourceReading("2026-06-15", "research", 5))
        readings.append(SourceReading("2026-06-22", "research", 5))

        found = detect_for_date(readings, "2026-06-22")

        self.assertEqual(1, len(found))
        self.assertEqual("2026-06-22", found[0].date)

    def test_anomaly_describes_itself_for_a_human(self):
        a = Anomaly(
            date="2026-06-22", source="research", count=8,
            baseline=477.0, weekday="Mon", ratio=8 / 477.0,
        )
        text = a.describe()
        self.assertIn("research", text)
        self.assertIn("8", text)
        self.assertIn("477", text)
        self.assertIn("Mon", text)


class RealHistoryRegressionTest(unittest.TestCase):
    """Pin the detector against the actual committed history.

    This is the test that matters. It asserts the detector finds the five known
    real anomaly rows and invents nothing beyond them -- i.e. that the
    signal-to-noise measured during design survives any future tuning.
    """

    @classmethod
    def setUpClass(cls):
        cls.readings = load_history(WEB_DIR)

    def test_history_loads(self):
        self.assertGreater(
            len(self.readings), 500,
            "expected hundreds of source-readings across the published history; "
            "if this is near zero, load_history is not finding web/data/*/summary.json",
        )

    def test_finds_exactly_the_known_incidents(self):
        found = {(a.date, a.source) for a in detect(self.readings)}

        missed = KNOWN_INCIDENTS - found
        self.assertEqual(
            set(), missed,
            f"detector missed known real incidents: {sorted(missed)}. The arXiv "
            "outage ran three weeks unnoticed; a detector that cannot see it in "
            "hindsight will not catch the next one.",
        )

        spurious = found - KNOWN_INCIDENTS
        self.assertEqual(
            set(), spurious,
            f"detector invented {len(spurious)} anomalies not in the known set: "
            f"{sorted(spurious)}. False positives train the operator to ignore it.",
        )

    def test_alert_volume_stays_low(self):
        found = detect(self.readings)
        dates = {r.date for r in self.readings}
        per_month = len(found) / max(len(dates), 1) * 30
        self.assertLess(
            per_month, 2.0,
            f"{per_month:.1f} alerts/month is too noisy to act on (measured 0.70 "
            "during design)",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.source_anomaly_test -v`

Expected: FAIL immediately at import — `ModuleNotFoundError: No module named 'agents.source_anomaly'`.

- [ ] **Step 3: Write the module**

Create `agents/source_anomaly.py`:

```python
"""Detect a source that has silently stopped producing items.

The pipeline can succeed, publish, and report `collection_status.overall:
"success"` while a source returns nothing. That is not hypothetical: arXiv
returned zero papers on three consecutive Monday reports (2026-06-22, 06-29,
07-06) and the regression ran three weeks before anyone noticed. Only
successful runs are ever committed -- the workflow reverts generated data on
failure -- so the published status field is always green and item counts are
the only honest signal.

Baselines key on the REPORT date's weekday, matching
`agents/gatherers/research_gatherer.py:148-153`: Saturday and Sunday reports
skip arXiv entirely, and Monday runs a 3-day Sat-Mon catch-up. Research volume
by report weekday runs roughly Sat 19 / Sun 11 / Mon 374 / Tue 996, so a flat
threshold reads every weekend as an outage -- measured against the real
215-day history it fires 35 times, nearly all false. Same-weekday baselines
fire 4 times, and all four are real.

Stdlib only: this runs in the dependency-light CI guard job and inside the
admin service.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Iterable, Sequence

__all__ = [
    "SourceReading",
    "Anomaly",
    "load_history",
    "report_weekday",
    "detect",
    "detect_for_date",
    "DEFAULT_WINDOW",
    "DEFAULT_MIN_BASELINE",
    "DEFAULT_RATIO",
]

# How many prior same-weekday samples form the baseline. Six is about six weeks
# of history for a given weekday -- long enough to be stable, short enough to
# follow a genuine change in volume rather than fighting it.
DEFAULT_WINDOW = 6

# Below this, a source's normal volume is too small for a ratio to mean
# anything: 3 items dropping to 0 is noise.
DEFAULT_MIN_BASELINE = 25

# Fraction of baseline below which a reading is anomalous. 0.35 was chosen
# against the real history: it catches every known incident while admitting
# no false positives.
DEFAULT_RATIO = 0.35

_WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class SourceReading:
    """One source's item count for one published report date."""

    date: str
    source: str
    count: int


@dataclass(frozen=True)
class Anomaly:
    """A reading that fell far below its same-weekday baseline."""

    date: str
    source: str
    count: int
    baseline: float
    weekday: str
    ratio: float

    def describe(self) -> str:
        return (
            f"{self.source} collected {self.count} items on {self.date} ({self.weekday}), "
            f"against a same-weekday median of {self.baseline:.0f} "
            f"({self.ratio * 100:.0f}% of normal)"
        )


def report_weekday(date_str: str) -> int:
    """Weekday index of the REPORT date. 0=Monday .. 6=Sunday.

    Deliberately the report date, not the coverage date: the gatherers' skip
    and catch-up logic branches on the report date, so baselines must be
    grouped the same way or Monday catch-ups get compared against Sundays.
    """
    year, month, day = (int(part) for part in date_str.split("-"))
    return _date(year, month, day).weekday()


def load_history(web_dir: Path) -> list[SourceReading]:
    """Read every published day's per-source item counts.

    Reads `collection_status.sources` and `.social_platforms` from
    `web/data/<date>/summary.json`. Days missing the block, or unparseable, are
    skipped -- a malformed old file should not blind the detector to the rest.
    """
    readings: list[SourceReading] = []
    data_dir = Path(web_dir) / "data"
    if not data_dir.is_dir():
        return readings

    for summary_path in sorted(data_dir.glob("*/summary.json")):
        report_date = summary_path.parent.name
        try:
            payload = json.loads(summary_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        status = payload.get("collection_status")
        if not isinstance(status, dict):
            continue

        # Treat top-level sources and individual social platforms alike: a
        # single dead platform (Bluesky, say) is exactly the kind of partial
        # failure the aggregate count hides.
        for key in ("sources", "social_platforms"):
            for entry in status.get(key) or []:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                count = entry.get("count")
                if not name or not isinstance(count, int):
                    continue
                readings.append(SourceReading(report_date, str(name), count))

    return readings


def _baselines(
    readings: Sequence[SourceReading],
) -> dict[tuple[str, int], list[tuple[str, int]]]:
    """Group readings by (source, report weekday), chronologically."""
    grouped: dict[tuple[str, int], list[tuple[str, int]]] = defaultdict(list)
    for reading in sorted(readings, key=lambda r: r.date):
        try:
            weekday = report_weekday(reading.date)
        except (ValueError, IndexError):
            continue  # not a YYYY-MM-DD directory
        grouped[(reading.source, weekday)].append((reading.date, reading.count))
    return grouped


def detect(
    readings: Iterable[SourceReading],
    *,
    window: int = DEFAULT_WINDOW,
    min_baseline: int = DEFAULT_MIN_BASELINE,
    ratio_threshold: float = DEFAULT_RATIO,
) -> list[Anomaly]:
    """Find every reading that collapsed against its same-weekday baseline.

    A reading is anomalous when it has at least four prior same-weekday
    samples, their median is at least `min_baseline`, and the reading is below
    `ratio_threshold` of that median. The four-sample floor keeps a new source
    from alarming before it has a history.
    """
    grouped = _baselines(list(readings))
    anomalies: list[Anomaly] = []

    for (source, weekday), series in grouped.items():
        for index, (date_str, count) in enumerate(series):
            prior = [c for _, c in series[max(0, index - window):index]]
            if len(prior) < 4:
                continue

            baseline = statistics.median(prior)
            if baseline < min_baseline:
                continue

            if count < baseline * ratio_threshold:
                anomalies.append(
                    Anomaly(
                        date=date_str,
                        source=source,
                        count=count,
                        baseline=float(baseline),
                        weekday=_WEEKDAY_NAMES[weekday],
                        ratio=count / baseline if baseline else 0.0,
                    )
                )

    return sorted(anomalies, key=lambda a: (a.date, a.source))


def detect_for_date(
    readings: Iterable[SourceReading],
    target_date: str,
    **kwargs,
) -> list[Anomaly]:
    """Anomalies for one report date, using all earlier readings as history."""
    material = [r for r in readings if r.date <= target_date]
    return [a for a in detect(material, **kwargs) if a.date == target_date]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.source_anomaly_test -v`

Expected: all PASS, including `RealHistoryRegressionTest.test_finds_exactly_the_known_incidents`.

If `test_finds_exactly_the_known_incidents` reports *spurious* anomalies, do not loosen the thresholds to make it green — investigate whether the extra finding is a real incident that was missed during design, and if so add it to `KNOWN_INCIDENTS` with a comment explaining what happened. If it reports *missed* incidents, the grouping logic is wrong; check that `report_weekday` is being applied to the directory name and not the coverage date inside the file.

- [ ] **Step 5: Commit**

```bash
git add agents/source_anomaly.py tests/source_anomaly_test.py
git commit -m "detect sources that silently stop producing

The pipeline can succeed, publish, and report collection_status success while
a source returns nothing -- arXiv did exactly that for three consecutive
Mondays before anyone noticed. Only successful runs are ever committed, so the
published status field is always green; item counts are the only real signal.

Baselines key on the report date's weekday to match the gatherers' own
Sat/Sun skip and Monday catch-up. Flat thresholds fire 35 times over the real
215-day history, nearly all weekend false positives; same-weekday baselines
fire 4 times and all four are real.

The regression test pins those four against committed history, and fails on a
fifth as loudly as on a miss -- a detector that cries wolf gets ignored, which
is the failure mode it exists to prevent."
```

---

### Task 2: The CI checker entry point

**Files:**
- Create: `scripts/check_source_anomalies.py`
- Test: manual invocation (below); the detector's own logic is covered by Task 1.

**Interfaces:**
- Consumes: `agents.source_anomaly.{load_history, detect_for_date, Anomaly}` from Task 1.
- Produces: `scripts/check_source_anomalies.py`, CLI:
  `--web-dir PATH` (default `web`), `--date YYYY-MM-DD` (default today in America/New_York), `--json`, `--alert`.
  Exit codes: `0` no anomalies, `1` anomalies found, `2` usage/IO error. `--alert` POSTs but never changes the exit code.

- [ ] **Step 1: Write the script**

Create `scripts/check_source_anomalies.py`:

```python
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
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.source_anomaly import Anomaly, detect_for_date, load_history  # noqa: E402

DEFAULT_ALERT_URL = "https://flybotwebhook.duffplex.com/alert/pipeline"


def _today_et() -> str:
    """Today in America/New_York without pulling in a tz dependency.

    ET is UTC-5 (EST) or UTC-4 (EDT). Using -5 year-round can only shift the
    date backward by an hour's worth of edge case near midnight, and the caller
    normally passes --date explicitly in CI anyway.
    """
    return (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d")


def _post_alert(anomalies: list[Anomaly], report_date: str, run_url: str) -> None:
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
```

Then: `chmod +x scripts/check_source_anomalies.py`

- [ ] **Step 2: Verify it detects a known-bad day**

Run: `python3 scripts/check_source_anomalies.py --web-dir web --date 2026-06-22`

Expected output (exit code 1):
```
DEGRADED: 1 source(s) below baseline on 2026-06-22
  - research collected 8 items on 2026-06-22 (Mon), against a same-weekday median of 477 (2% of normal)
```

Confirm with: `echo "exit=$?"` → `exit=1`.

- [ ] **Step 3: Verify it passes a known-good day**

Run: `python3 scripts/check_source_anomalies.py --web-dir web --date 2026-07-27; echo "exit=$?"`

Expected: `OK: all sources within their same-weekday baselines on 2026-07-27` and `exit=0`.

- [ ] **Step 4: Verify the JSON mode and the no-data case**

Run:
```bash
python3 scripts/check_source_anomalies.py --web-dir web --date 2026-06-29 --json
python3 scripts/check_source_anomalies.py --web-dir web --date 1999-01-01; echo "exit=$?"
```

Expected: valid JSON with `"anomaly_count": 1` for the first; `No published data for 1999-01-01; nothing to check.` and `exit=0` for the second — an unpublished date is not a failure.

- [ ] **Step 5: Verify alerting is inert without a token**

Run: `PIPELINE_ALERT_TOKEN= python3 scripts/check_source_anomalies.py --web-dir web --date 2026-06-22 --alert`

Expected: the DEGRADED report followed by `PIPELINE_ALERT_TOKEN not set; skipping alert POST.` and exit 1. **No network call is made.** Do not test a live POST from a laptop — it would page the on-call agent with a synthetic alert.

- [ ] **Step 6: Commit**

```bash
git add scripts/check_source_anomalies.py
git commit -m "add the post-run source anomaly check

CLI over the detector: reports anomalies for one date and, with --alert, POSTs
status degraded to the existing ingress. Same URL, same bearer, same
fail-closed-on-missing-token behavior as the failure alert it sits beside --
that one fires when the job fails, this one when the job succeeds and the data
is wrong.

Exit 1 on anomalies so a caller can branch, but --alert never changes the exit
code and delivery failures never propagate: a paging problem must not turn a
degraded run into a failed one."
```

---

### Task 3: Wire the check into the pipeline

**Files:**
- Modify: `.github/workflows/daily-pipeline.yml` (insert after the step named `Assert today's report is published on origin/main`, which begins at `:470`)
- Modify: `.github/workflows/tests.yml`

**Interfaces:**
- Consumes: `scripts/check_source_anomalies.py` from Task 2.
- Produces: nothing.

- [ ] **Step 1: Add the workflow step**

In `.github/workflows/daily-pipeline.yml`, immediately **before** the step named `Alert on pipeline failure` (at `:500`), insert:

```yaml
      - name: Check for silently degraded sources
        # Runs only when the pipeline SUCCEEDED and published. The failure alert
        # below covers red runs; this covers green runs that published bad data --
        # arXiv returned zero for three consecutive Mondays while every run stayed
        # green (2026-06-22/29, 07-06). Never fails the job: the report is live and
        # correct-ish, and a false red here would train us to ignore the real one.
        if: success() && steps.schedule_gate.outputs.should_run == 'true'
        continue-on-error: true
        env:
          PIPELINE_ALERT_TOKEN: ${{ secrets.PIPELINE_ALERT_TOKEN }}
          PIPELINE_ALERT_URL: ${{ vars.PIPELINE_ALERT_URL || 'https://flybotwebhook.duffplex.com/alert/pipeline' }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
          TARGET_DATE_INPUT: ${{ inputs.target_date }}
        run: |
          set -uo pipefail
          report_date="${TARGET_DATE_INPUT:-$(TZ=America/New_York date +%F)}"
          python3 scripts/check_source_anomalies.py \
            --web-dir ./web --date "$report_date" --alert || true
```

Note `continue-on-error: true` **and** the trailing `|| true`: the script exits 1 by design when it finds anomalies, and a degraded-but-published day must not turn the pipeline red.

- [ ] **Step 2: Add the guard test to CI**

In `.github/workflows/tests.yml`, after the `Deploy signature gate guard` step (added by the plan-0 work; if that has not landed, add after the `extract_json_str_test` step instead):

```yaml
      - name: Source anomaly detector
        run: python3 -m unittest tests.source_anomaly_test -v
```

- [ ] **Step 3: Verify both workflows are valid YAML**

Run:
```bash
python3 -c "
import yaml
for f in ['.github/workflows/daily-pipeline.yml', '.github/workflows/tests.yml']:
    yaml.safe_load(open(f)); print(f, 'valid')
"
```

Expected: both `valid`. If PyYAML is unavailable locally, rely on the push validating in CI.

- [ ] **Step 4: Confirm the new step sits in the right place**

Run: `grep -n "      - name:" .github/workflows/daily-pipeline.yml | tail -4`

Expected order: `Assert today's report is published on origin/main`, then `Check for silently degraded sources`, then `Alert on pipeline failure`. The degradation check must come after the publish assertion (there is nothing to check before publish) and before the failure alert (which only runs on `failure()`).

- [ ] **Step 5: Run the full guard suite**

Run: `python3 -m unittest tests.source_anomaly_test -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/daily-pipeline.yml .github/workflows/tests.yml
git commit -m "ci: check for silently degraded sources after publishing

Fires on green runs that published bad data, which is the gap the existing
failure alert cannot see: the arXiv outage exited 0 every time.

continue-on-error plus || true because the script exits 1 by design when it
finds something. A degraded day is still a published day, and turning it red
would make the signal indistinguishable from a real pipeline failure."
```

---

## Self-Review

**Spec coverage.** Implements spec §5 panel 1 (the detector behind the source-health timeline) and §6 (alerting: same path, same bearer, `status: "degraded"`, dedup). The dashboard *view* over this data is plan 4; this plan delivers the module it will import, which is what makes "one detector, two callers" real rather than aspirational.

**Dedup.** Spec §6 requires one notification per incident, not one per day. This plan alerts per-run, so a three-week outage would POST 21 times. That is deliberate: dedup state has to live somewhere durable, and the admin service's SQLite store (plan 2) is the right home. Until then the pipeline's own `if: success()` gate means at most one POST per day, and the Hermes handoff document (written last, after this is observed working) will specify collapsing repeats on `(source, first_seen)`. **Carry this into plan 2 as an explicit task.**

**Placeholders.** None. Every step has literal file content or a runnable command with expected output.

**Type/name consistency.** `SourceReading`, `Anomaly`, `load_history`, `report_weekday`, `detect`, `detect_for_date` are defined in Task 1 Step 3 and imported under exactly those names in Task 1's test and in Task 2's script. `Anomaly` field names (`date`, `source`, `count`, `baseline`, `weekday`, `ratio`) are used identically in `describe()`, the test's constructor call, and the alert payload builder. The CLI flags in Task 2 Step 1 (`--web-dir`, `--date`, `--json`, `--alert`) match every invocation in Steps 2-5 and in Task 3's workflow step. Exit-code semantics (0/1/2) are stated in the docstring and relied on by Task 3's `|| true`.

**One judgment call to flag.** `_today_et()` approximates Eastern as UTC-5 year-round rather than depending on `zoneinfo` data being present. During EDT this can name the previous day for runs between 00:00 and 01:00 ET. CI always passes `--date` explicitly, so the fallback only matters for ad-hoc local runs; the docstring says so.
