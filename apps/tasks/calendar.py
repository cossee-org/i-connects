"""Grid-building for the task calendar view (year / month / week / day).

Buckets tasks (already filtered to those with a calendar-relevant date) by
local date, using each task's ``deadline`` if set, otherwise its
``start_date``. Weeks start on Monday throughout.
"""

from __future__ import annotations

import calendar as _calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.utils import timezone

VIEWS = ("year", "month", "week", "day")


@dataclass
class CalendarDay:
    date: date
    in_current_month: bool
    is_today: bool
    tasks: list = field(default_factory=list)


def task_calendar_date(task) -> date | None:
    """Return the local date a task should be plotted on, or ``None``."""
    dt = task.deadline or task.start_date
    if dt is None:
        return None
    return timezone.localtime(dt).date() if timezone.is_aware(dt) else dt.date()


def _sort_key(task):
    return task.deadline or task.start_date


def bucket_tasks_by_day(tasks) -> dict:
    """Group tasks by their calendar date, each day's list sorted by time."""
    by_day: dict = {}
    for task in tasks:
        day = task_calendar_date(task)
        if day is not None:
            by_day.setdefault(day, []).append(task)
    for day_tasks in by_day.values():
        day_tasks.sort(key=_sort_key)
    return by_day


def build_month_grid(year: int, month: int, tasks) -> list[list[CalendarDay]]:
    """Build a week-by-week grid of :class:`CalendarDay` for ``year``/``month``.

    ``tasks`` should already be filtered to tasks with a calendar date set;
    this only buckets them by day.
    """
    by_day = bucket_tasks_by_day(tasks)
    today = timezone.localdate()
    cal = _calendar.Calendar(firstweekday=0)  # Monday
    weeks = []
    for week in cal.monthdatescalendar(year, month):
        days = [
            CalendarDay(
                date=day,
                in_current_month=day.month == month,
                is_today=day == today,
                tasks=by_day.get(day, []),
            )
            for day in week
        ]
        weeks.append(days)
    return weeks


def build_year_grid(year: int, tasks) -> list[dict]:
    """Build 12 mini month grids (one per month) for ``year``."""
    return [
        {
            "month": month,
            "label": date(year, month, 1).strftime("%B"),
            "first_day": date(year, month, 1),
            "weeks": build_month_grid(year, month, tasks),
        }
        for month in range(1, 13)
    ]


def build_week_grid(anchor: date, tasks) -> list[CalendarDay]:
    """Build the 7 days (Mon–Sun) of the week containing ``anchor``."""
    start = anchor - timedelta(days=anchor.weekday())
    by_day = bucket_tasks_by_day(tasks)
    today = timezone.localdate()
    return [
        CalendarDay(
            date=day,
            in_current_month=True,
            is_today=day == today,
            tasks=by_day.get(day, []),
        )
        for day in (start + timedelta(days=offset) for offset in range(7))
    ]


def build_day_tasks(anchor: date, tasks) -> list:
    """Return the tasks plotted on exactly ``anchor``, sorted by time."""
    return bucket_tasks_by_day(tasks).get(anchor, [])


def parse_view_date(raw: str | None) -> date:
    """Parse a ``YYYY-MM-DD`` date param, defaulting to today on any error."""
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return timezone.localdate()


def clamp_month(year: int, month: int) -> tuple[int, int]:
    """Normalize an out-of-range month (e.g. month=13) by rolling into year."""
    month0 = month - 1
    year += month0 // 12
    month = month0 % 12 + 1
    return year, month


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    return clamp_month(year, month + delta)


def shift_date(view: str, anchor: date, delta: int) -> date:
    """Move ``anchor`` by one unit of ``view`` (year/month/week/day) * delta."""
    if view == "year":
        return date(anchor.year + delta, 1, 1)
    if view == "month":
        year, month = shift_month(anchor.year, anchor.month, delta)
        return date(year, month, 1)
    if view == "week":
        return anchor + timedelta(weeks=delta)
    if view == "day":
        return anchor + timedelta(days=delta)
    raise ValueError(f"Unknown calendar view: {view!r}")
