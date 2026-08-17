"""Month-grid building for the task calendar view.

Buckets tasks (already filtered to those with a calendar-relevant date) into
a 6-week x 7-day grid for a given month, using each task's ``deadline`` if
set, otherwise its ``start_date``.
"""

from __future__ import annotations

import calendar as _calendar
from dataclasses import dataclass, field
from datetime import date

from django.utils import timezone


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


def build_month_grid(year: int, month: int, tasks) -> list[list[CalendarDay]]:
    """Build a 6-week grid of :class:`CalendarDay` for ``year``/``month``.

    ``tasks`` should already be filtered to tasks with a calendar date set;
    this only buckets them by day. Weeks start on Monday.
    """
    by_day: dict[date, list] = {}
    for task in tasks:
        day = task_calendar_date(task)
        if day is not None:
            by_day.setdefault(day, []).append(task)

    today = timezone.localdate()
    cal = _calendar.Calendar(firstweekday=0)  # Monday
    weeks = []
    for week in cal.monthdatescalendar(year, month):
        days = [
            CalendarDay(
                date=day,
                in_current_month=day.month == month,
                is_today=day == today,
                tasks=sorted(
                    by_day.get(day, []),
                    key=lambda t: (t.deadline or t.start_date),
                ),
            )
            for day in week
        ]
        weeks.append(days)
    return weeks


def clamp_month(year: int, month: int) -> tuple[int, int]:
    """Normalize an out-of-range month (e.g. month=13) by rolling into year."""
    month0 = month - 1
    year += month0 // 12
    month = month0 % 12 + 1
    return year, month


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    return clamp_month(year, month + delta)
