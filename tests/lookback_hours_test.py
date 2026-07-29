"""`LOOKBACK_HOURS` must actually bound the collection window.

It was documented as "Data window in hours (default: 24)" and threaded from
`run_pipeline.py` through the orchestrator into every gatherer's `__init__` — then
never read. The window was hardcoded to the whole coverage day, so setting the
variable did nothing at all.

The contract now:
  * the window ENDS at the close of the coverage day (the day before the report),
    which is what makes a report "yesterday's news" — raising the lookback must not
    pull tomorrow's items in;
  * it STARTS `lookback_hours` before that end;
  * at the default 24 the boundaries are byte-identical to the old behaviour, so no
    published report changes.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.base import BaseGatherer


class _Gatherer(BaseGatherer):
    """Minimal concrete subclass — the window is set entirely in __init__."""

    @property
    def category(self) -> str:
        return "test"

    async def gather(self):
        return []


def _make(target_date="2026-07-28", lookback_hours=24):
    return _Gatherer(
        config_dir="/tmp",
        data_dir="/tmp",
        lookback_hours=lookback_hours,
        target_date=target_date,
    )


def _legacy_window(target_date):
    """The window exactly as it was computed before lookback_hours was honoured."""
    coverage = datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=1)
    return (
        coverage.replace(hour=0, minute=0, second=0, microsecond=0),
        coverage.replace(hour=23, minute=59, second=59, microsecond=999999),
    )


@pytest.mark.parametrize(
    "target_date",
    [
        "2026-07-28",  # ordinary Tuesday
        "2026-01-01",  # crosses a year boundary
        "2024-02-29",  # leap day
        "2026-03-08",  # US DST spring-forward
        "2026-11-02",  # US DST fall-back
        "2026-12-31",
    ],
)
def test_default_24_is_identical_to_the_old_behaviour(target_date):
    """The default must not move a single microsecond — every report depends on it."""
    g = _make(target_date)
    assert (g.start_time, g.end_time) == _legacy_window(target_date)


def test_default_covers_exactly_the_day_before_the_report():
    g = _make("2026-07-28")
    assert g.report_date == "2026-07-28"
    assert g.coverage_date == "2026-07-27"
    assert g.start_time == datetime(2026, 7, 27, 0, 0, 0)
    assert g.end_time == datetime(2026, 7, 27, 23, 59, 59, 999999)


@pytest.mark.parametrize("hours", [6, 12, 24, 48, 72, 168])
def test_window_spans_exactly_the_requested_hours(hours):
    g = _make("2026-07-28", lookback_hours=hours)
    # end_time is inclusive and sits 1µs short of the boundary, so add it back.
    span = (g.end_time + timedelta(microseconds=1)) - g.start_time
    assert span == timedelta(hours=hours)


@pytest.mark.parametrize("hours", [6, 24, 48, 168])
def test_end_of_window_never_moves(hours):
    """Reaching further back must not reach further FORWARD.

    If the end drifted with the lookback, a large value would start pulling in items
    from the report day itself and the report would stop being about yesterday.
    """
    assert _make("2026-07-28", lookback_hours=hours).end_time == datetime(
        2026, 7, 27, 23, 59, 59, 999999
    )


def test_larger_lookback_reaches_into_earlier_days():
    g = _make("2026-07-28", lookback_hours=72)
    assert g.start_time == datetime(2026, 7, 25, 0, 0, 0)
    # An item from two days before the coverage day is now in range...
    assert g.is_in_date_range(datetime(2026, 7, 25, 12, 0, 0))
    # ...but one from before the window still is not.
    assert not g.is_in_date_range(datetime(2026, 7, 24, 23, 59, 59))


def test_short_lookback_narrows_to_the_end_of_the_coverage_day():
    g = _make("2026-07-28", lookback_hours=6)
    assert g.start_time == datetime(2026, 7, 27, 18, 0, 0)
    assert g.is_in_date_range(datetime(2026, 7, 27, 20, 0, 0))
    assert not g.is_in_date_range(datetime(2026, 7, 27, 17, 59, 59))


def test_boundaries_are_inclusive():
    g = _make("2026-07-28")
    assert g.is_in_date_range(g.start_time)
    assert g.is_in_date_range(g.end_time)
    assert not g.is_in_date_range(g.start_time - timedelta(microseconds=1))
    assert not g.is_in_date_range(g.end_time + timedelta(microseconds=1))


@pytest.mark.parametrize("hours", [0, -1, -24])
def test_non_positive_lookback_is_refused(hours):
    """An empty window would collect nothing and report success while doing it."""
    with pytest.raises(ValueError, match="must be positive"):
        _make("2026-07-28", lookback_hours=hours)


def test_no_target_date_still_derives_from_today():
    g = _Gatherer(config_dir="/tmp", data_dir="/tmp", lookback_hours=24)
    expected = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    assert g.coverage_date == expected
    span = (g.end_time + timedelta(microseconds=1)) - g.start_time
    assert span == timedelta(hours=24)
