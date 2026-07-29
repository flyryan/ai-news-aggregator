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

import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "web"


def _load_source_anomaly():
    """Import the module by path, bypassing the `agents` package __init__.

    `agents/__init__.py` imports llm_client, which imports httpx. This test runs
    in the dependency-light CI guard job where httpx is absent, and the detector
    itself is stdlib-only by design -- so load it directly rather than dragging
    in the whole package. Same spirit as extract_json_str_test.py, which parses
    agents/base.py with `ast` for the same reason.
    """
    spec = importlib.util.spec_from_file_location(
        "source_anomaly", REPO_ROOT / "agents" / "source_anomaly.py"
    )
    module = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves field annotations through
    # sys.modules[cls.__module__], and blows up on None if the module is not
    # there yet.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_mod = _load_source_anomaly()
Anomaly = _mod.Anomaly
SourceReading = _mod.SourceReading
detect = _mod.detect
detect_for_date = _mod.detect_for_date
load_history = _mod.load_history
report_weekday = _mod.report_weekday

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
