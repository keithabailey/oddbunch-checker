"""Substitution window: Monday 2pm local until Tuesday 11:55pm Eastern."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

CLOSE_TZ = ZoneInfo("America/Toronto")
OPEN_TIME = dt.time(14, 0)
CLOSE_TIME = dt.time(23, 55)


def window_for(now: dt.datetime, local_tz: str) -> tuple[dt.datetime, dt.datetime]:
    tz = ZoneInfo(local_tz)
    local = now.astimezone(tz)
    monday = local.date() - dt.timedelta(days=local.weekday())
    opens = dt.datetime.combine(monday, OPEN_TIME, tzinfo=tz)
    closes = dt.datetime.combine(monday + dt.timedelta(days=1), CLOSE_TIME, tzinfo=CLOSE_TZ)
    return opens, closes


def is_open(now: dt.datetime, local_tz: str) -> bool:
    opens, closes = window_for(now, local_tz)
    return opens <= now < closes


def fmt(t: dt.datetime, local_tz: str) -> str:
    local = t.astimezone(ZoneInfo(local_tz))
    return local.strftime("%a %d %b %-I:%M%p %Z").replace("AM", "am").replace("PM", "pm")
