"""Tests for the ICS (iCalendar) milestone export."""

from __future__ import annotations

from datetime import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.enums import ProjectStatus
from apps.projects.services import create_project_with_owner
from apps.tasks.ics import build_ics
from apps.tasks.models import Milestone


def _make_project(owner, *, name="P"):
    return create_project_with_owner(
        user=owner,
        form_data={"name": name, "status": ProjectStatus.ACTIVE, "is_public": True},
    )


@pytest.mark.django_db
def test_build_ics_with_single_milestone_produces_vcalendar(user):
    project = _make_project(user)
    start = timezone.make_aware(datetime(2026, 4, 15, 9, 0, 0))
    end = timezone.make_aware(datetime(2026, 4, 16, 17, 0, 0))
    milestone = Milestone.objects.create(
        project=project, name="Kickoff", start_date=start, end_date=end
    )

    ics_text = build_ics([milestone], calendar_name="Demo")

    assert "BEGIN:VCALENDAR" in ics_text
    assert "END:VCALENDAR" in ics_text
    assert ics_text.count("BEGIN:VEVENT") == 1
    assert ics_text.count("END:VEVENT") == 1
    # DTSTART line exists and carries an iCal UTC date-time.
    dtstart_lines = [
        ln for ln in ics_text.split("\r\n") if ln.startswith("DTSTART:")
    ]
    assert len(dtstart_lines) == 1
    assert "20260415" in dtstart_lines[0]
    assert dtstart_lines[0].endswith("Z")


@pytest.mark.django_db
def test_build_ics_escapes_special_characters(user):
    project = _make_project(user)
    start = timezone.make_aware(datetime(2026, 4, 15, 9, 0, 0))
    milestone = Milestone.objects.create(
        project=project,
        name="Hello, world; test\\",
        start_date=start,
    )

    ics_text = build_ics([milestone], calendar_name="Demo")

    summary_line = next(
        ln for ln in ics_text.split("\r\n") if ln.startswith("SUMMARY:")
    )
    assert summary_line == "SUMMARY:Hello\\, world\\; test\\\\"


@pytest.mark.django_db
def test_build_ics_skips_milestones_without_dates(user):
    project = _make_project(user)
    with_date = Milestone.objects.create(
        project=project,
        name="Has date",
        start_date=timezone.make_aware(datetime(2026, 4, 15, 9, 0, 0)),
    )
    Milestone.objects.create(project=project, name="No date")

    ics_text = build_ics(
        [with_date, Milestone.objects.get(name="No date")],
        calendar_name="Demo",
    )

    assert ics_text.count("BEGIN:VEVENT") == 1
    assert "Has date" in ics_text
    assert "No date" not in ics_text


@pytest.mark.django_db
def test_milestone_calendar_endpoint_returns_ics(client, user):
    project = _make_project(user)
    Milestone.objects.create(
        project=project,
        name="Kickoff",
        start_date=timezone.make_aware(datetime(2026, 4, 15, 9, 0, 0)),
    )
    client.force_login(user)

    response = client.get(
        reverse("tasks:milestone_calendar", kwargs={"project_pk": project.pk})
    )

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/calendar")
    assert b"BEGIN:VCALENDAR" in response.content


@pytest.mark.django_db
def test_milestone_calendar_requires_project_membership(client, user, other_user):
    project = _make_project(user)
    Milestone.objects.create(
        project=project,
        name="Kickoff",
        start_date=timezone.make_aware(datetime(2026, 4, 15, 9, 0, 0)),
    )
    client.force_login(other_user)

    response = client.get(
        reverse("tasks:milestone_calendar", kwargs={"project_pk": project.pk})
    )

    assert response.status_code == 403
