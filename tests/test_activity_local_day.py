"""A claim is stamped with the LOCAL date (ctx.now is local), so its activity window must open
at LOCAL midnight of that date. Opening it at UTC midnight shifted the window by the UTC offset:
east of UTC, work done right after claiming did not count until UTC's day caught up."""
import datetime as dt
import os
import time

import pytest

from crossbox import activity


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs time.tzset (POSIX)")
@pytest.mark.parametrize("zone,offset_h", [("Etc/GMT-14", 14), ("Etc/GMT+10", -10), ("UTC", 0)])
def test_the_window_opens_at_local_midnight_of_the_claim_date(monkeypatch, zone, offset_h):
    monkeypatch.setenv("TZ", zone)
    time.tzset()
    try:
        d = dt.date(2026, 1, 2)
        want = dt.datetime(2026, 1, 2, tzinfo=dt.timezone(dt.timedelta(hours=offset_h))).timestamp()
        assert activity._midnight(d) == want
    finally:
        monkeypatch.undo()
        time.tzset()
