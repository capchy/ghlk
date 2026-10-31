"""Chooses which forecast publication applies to each target period.

Publication times are taken from the data; no publication schedule is assumed.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from app.core.timeutils import decision_cutoff


def day_ahead_cutoffs(
    start_times: pd.Series, business_tz: ZoneInfo, cutoff_time: time, cutoff_tz: ZoneInfo
) -> pd.Series:
    """Cutoff for each target: ``cutoff_time`` on the day before its local delivery date.

    Computed per distinct delivery date with zoneinfo, so DST changes are exact.
    """
    if start_times.empty:
        return pd.Series(dtype="datetime64[ns, UTC]")
    delivery_dates = start_times.dt.tz_convert(business_tz).dt.date
    cutoffs = {d: pd.Timestamp(decision_cutoff(d, cutoff_time, cutoff_tz)) for d in delivery_dates.unique()}
    return pd.to_datetime(delivery_dates.map(cutoffs), utc=True)


def _latest_per_target(publications: pd.DataFrame) -> pd.DataFrame:
    if publications.empty:
        return publications
    idx = publications.groupby("start_time")["publish_time"].idxmax()
    return publications.loc[idx].sort_values("start_time").reset_index(drop=True)


def select_as_of_cutoff(
    publications: pd.DataFrame, business_tz: ZoneInfo, cutoff_time: time, cutoff_tz: ZoneInfo
) -> pd.DataFrame:
    """Historical selection: for each target, the latest publication with
    ``publish_time <= cutoff``. Later publications are never used."""
    if publications.empty:
        return publications.assign(cutoff=pd.Series(dtype="datetime64[ns, UTC]"))
    df = publications.assign(cutoff=day_ahead_cutoffs(publications["start_time"], business_tz, cutoff_time, cutoff_tz))
    return _latest_per_target(df[df["publish_time"] <= df["cutoff"]])


def select_latest(publications: pd.DataFrame, as_of: datetime | None = None) -> pd.DataFrame:
    """Upcoming selection: latest publication per target, optionally published at or before ``as_of``."""
    if as_of is not None and not publications.empty:
        publications = publications[publications["publish_time"] <= pd.Timestamp(as_of)]
    return _latest_per_target(publications)
