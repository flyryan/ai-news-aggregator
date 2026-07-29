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
fire 5 times, and all five are real.

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
