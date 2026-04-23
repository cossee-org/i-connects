"""ICS (iCalendar, RFC 5545) export for project milestones.

Hand-rolled because the output is simple enough not to warrant a new
dependency. Produces a ``text/calendar`` payload that researchers can
subscribe to from Google Calendar, Apple Calendar, or Outlook.

Note: RFC 5545 line folding at 75 octets is not implemented here — most
modern calendar clients accept un-folded lines. Revisit if a client
rejects the feed.
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from typing import Iterable

from django.utils import timezone

from apps.tasks.models import Milestone


_LINE_ENDING = "\r\n"


def _escape(value: str) -> str:
    """Escape special characters for an iCalendar TEXT value (RFC 5545 §3.3.11)."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def _escape_multiline(value: str) -> str:
    """Escape TEXT value and fold newlines to the literal ``\\n`` sequence."""
    return _escape(value).replace("\r\n", "\\n").replace("\n", "\\n")


def _format_utc(dt: datetime) -> str:
    """Format a datetime as a UTC iCalendar ``DATE-TIME`` (``YYYYMMDDTHHMMSSZ``)."""
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt.astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_ics(milestones: Iterable[Milestone], *, calendar_name: str) -> str:
    """Build an RFC 5545 VCALENDAR string from an iterable of milestones.

    Milestones with no ``start_date`` and no ``end_date`` are skipped
    (there is nothing to subscribe to). If only one endpoint is set, it
    is used for both DTSTART and DTEND so the event shows at a single
    point in time.
    """
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//i-CONNECTS//Milestones//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_escape(calendar_name)}",
    ]

    dtstamp = _format_utc(timezone.now())

    for milestone in milestones:
        start = milestone.start_date
        end = milestone.end_date
        if start is None and end is None:
            continue
        if start is None:
            start = end
        if end is None:
            end = start

        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:milestone-{milestone.pk}@iconnects")
        lines.append(f"DTSTAMP:{dtstamp}")
        lines.append(f"DTSTART:{_format_utc(start)}")
        lines.append(f"DTEND:{_format_utc(end)}")
        lines.append(f"SUMMARY:{_escape(milestone.name)}")
        if milestone.description:
            lines.append(f"DESCRIPTION:{_escape_multiline(milestone.description)}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return _LINE_ENDING.join(lines) + _LINE_ENDING
