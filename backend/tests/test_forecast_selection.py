from datetime import time
from zoneinfo import ZoneInfo

import pandas as pd

from app.core.timeutils import decision_cutoff
from app.services.forecast_selection_service import day_ahead_cutoffs, select_as_of_cutoff, select_latest
from tests.conftest import utc

LONDON = ZoneInfo("Europe/London")
NINE = time(9, 0)


def ts(value: str) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC")


def pubs(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "publish_time": [ts(p) for p, _, _ in rows],
            "start_time": [ts(s) for _, s, _ in rows],
            "forecast_mw": [g for _, _, g in rows],
        }
    )


SPEC_EXAMPLE = pubs(
    [
        ("2026-10-14T05:30Z", "2026-10-15T10:00Z", 11800),
        ("2026-10-14T07:30Z", "2026-10-15T10:00Z", 12000),
        ("2026-10-14T09:30Z", "2026-10-15T10:00Z", 12193),
        ("2026-10-14T11:30Z", "2026-10-15T10:00Z", 12362),
    ]
)


def test_latest_eligible_forecast_before_cutoff_is_selected() -> None:
    # 09:00 BST on 14 Oct = 08:00 UTC.
    selected = select_as_of_cutoff(SPEC_EXAMPLE, LONDON, NINE, LONDON)
    assert selected["forecast_mw"].tolist() == [12000]
    assert selected["publish_time"].tolist() == [ts("2026-10-14T07:30Z")]


def test_later_cutoff_picks_later_vintage_and_never_future_ones() -> None:
    selected = select_as_of_cutoff(SPEC_EXAMPLE, LONDON, time(12, 0), LONDON)  # 11:00 UTC
    assert selected["forecast_mw"].tolist() == [12193]  # the 11:30 UTC publication is excluded


def test_publication_exactly_at_cutoff_is_eligible() -> None:
    df = pubs([("2026-10-14T08:00Z", "2026-10-15T10:00Z", 1)])
    assert len(select_as_of_cutoff(df, LONDON, NINE, LONDON)) == 1


def test_target_without_eligible_forecast_is_absent() -> None:
    df = pubs([("2026-10-14T09:30Z", "2026-10-15T10:00Z", 1)])
    assert select_as_of_cutoff(df, LONDON, NINE, LONDON).empty
    assert select_as_of_cutoff(df.iloc[0:0], LONDON, NINE, LONDON).empty


def test_input_versions_are_not_modified() -> None:
    before = SPEC_EXAMPLE.copy()
    select_as_of_cutoff(SPEC_EXAMPLE, LONDON, NINE, LONDON)
    pd.testing.assert_frame_equal(SPEC_EXAMPLE, before)


def test_cutoffs_use_local_delivery_date_across_dst() -> None:
    starts = pd.Series([ts("2026-10-26T12:00Z"), ts("2026-10-25T00:00Z"), ts("2026-10-24T23:30Z")])
    cutoffs = day_ahead_cutoffs(starts, LONDON, NINE, LONDON).tolist()
    # Delivery 26 Oct -> 25 Oct 09:00 GMT; 25 Oct 00:00Z is 01:00 BST on 25 Oct -> 24 Oct 09:00 BST;
    # 24 Oct 23:30Z is 00:30 BST on 25 Oct -> same cutoff.
    assert cutoffs == [ts("2026-10-25T09:00Z"), ts("2026-10-24T08:00Z"), ts("2026-10-24T08:00Z")]


def test_vectorised_cutoffs_match_scalar_helper_hour_by_hour() -> None:
    hours = pd.Series(
        list(pd.date_range("2026-03-27", "2026-03-31", freq="h", tz="UTC"))
        + list(pd.date_range("2026-10-23", "2026-10-27", freq="h", tz="UTC"))
    )
    vector = day_ahead_cutoffs(hours, LONDON, time(9, 30), LONDON)
    for start, cutoff in zip(hours, vector, strict=True):
        delivery = start.tz_convert(LONDON).date()
        assert cutoff.to_pydatetime() == decision_cutoff(delivery, time(9, 30), LONDON)


def test_cutoff_timezone_can_differ_from_business_timezone() -> None:
    starts = pd.Series([ts("2026-07-15T12:00Z")])
    assert day_ahead_cutoffs(starts, LONDON, NINE, ZoneInfo("UTC")).tolist() == [ts("2026-07-14T09:00Z")]


def test_select_latest_with_and_without_as_of() -> None:
    assert select_latest(SPEC_EXAMPLE)["forecast_mw"].tolist() == [12362]
    as_of = utc(2026, 10, 14, 10)
    assert select_latest(SPEC_EXAMPLE, as_of)["forecast_mw"].tolist() == [12193]
    assert select_latest(SPEC_EXAMPLE, utc(2026, 10, 1)).empty
