"""Made-up context for every email in ``templates/emails/``.

Used by the development email preview (``/dev/emails/``) and by the tests
that render every email, so adding an email means adding its sample here.
The objects are plain stand-ins with just the attributes the templates
read; nothing touches the database.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from apps.core.emails import absolute_url

MOUNTAIN = "America/Denver"


def _at(days: int, hour: int = 17) -> datetime:
    base = datetime(2026, 12, 1, hour, 0, tzinfo=ZoneInfo(MOUNTAIN))
    return base + timedelta(days=days)


def _samples() -> dict[str, dict]:
    lead = SimpleNamespace(name="Alice Moreno", email="alice.moreno@example.edu")
    member = SimpleNamespace(
        name="Jordan Lee", email="jordan.lee@example.edu", display_name="Jordan Lee"
    )
    requester = SimpleNamespace(name="Sam Patel", email="sam.patel@example.edu")
    project = SimpleNamespace(pk=12, name="Coral Reef Survey")

    def task(pk, name, days, *, my_days=None):
        deadline = _at(days)
        return SimpleNamespace(
            pk=pk,
            name=name,
            project=project,
            project_id=project.pk,
            deadline=deadline,
            timezone=MOUNTAIN,
            my_deadline=_at(my_days) if my_days is not None else deadline,
            my_deadline_tz=MOUNTAIN,
        )

    photos = task(88, "Transect photos: batch 3", 3)
    counts = task(89, "Count corals in quadrats 1-12", -2)
    report = task(90, "Draft the methods section", -5, my_days=-1)

    def task_url(t):
        return absolute_url(f"/projects/{project.pk}/tasks/{t.pk}/")

    join_request = SimpleNamespace(
        message="I'm on the 2027 field team and will be processing the transect "
        "photos from Lizard Island.",
        decision_note="We're full for this season. Please ask again in March.",
        get_requested_privilege_display=lambda: "Member",
    )
    invitation = SimpleNamespace(
        added_by=lead.name,
        get_privilege_display=lambda: "Member",
    )
    announcement = SimpleNamespace(
        headline="Scheduled maintenance on Saturday",
        details="i-CONNECTS will be unavailable from 8:00 to 10:00 AM MST on "
        "Saturday, December 5 while we upgrade the server.\n\n"
        "Anything you save before then is safe.",
        external_link=None,
        url=None,
    )
    notifications = [
        SimpleNamespace(
            headline="Alice Moreno verified “Transect photos: batch 2”",
            message="Alice Moreno verified “Transect photos: batch 2”",
            project=project,
            url=f"/projects/{project.pk}/tasks/87/",
        ),
        SimpleNamespace(
            headline="New wiki page: Field safety checklist",
            message="New wiki page: Field safety checklist",
            project=project,
            url=None,
        ),
    ]
    site = absolute_url("/")
    return {
        "welcome": {"user": member, "site_url": site},
        "confirm_email": {
            "user": member,
            "confirm_url": absolute_url("/accounts/confirm/MTI/c8k2-4f1e9a7b/"),
        },
        "password_reset": {
            "user": member,
            "reset_url": absolute_url("/accounts/reset/MTI/c8k2-4f1e9a7b/"),
        },
        "account_locked": {
            "user": member,
            "unlock_url": absolute_url("/accounts/unlock/Qm9yZWFsaXM/"),
        },
        "invitation": {
            "invitation": invitation,
            "project": project,
            "url": absolute_url("/invites/TUcjd4OMU7XK3CoY/"),
        },
        "join_request": {
            "join_request": join_request,
            "project": project,
            "requester": requester,
            "url": absolute_url(f"/projects/{project.pk}/join-requests/"),
        },
        "join_request_approved": {
            "join_request": join_request,
            "project": project,
            "approved": True,
            "url": absolute_url(f"/projects/{project.pk}/"),
        },
        "join_request_declined": {
            "join_request": join_request,
            "project": project,
            "approved": False,
            "url": absolute_url(f"/projects/{project.pk}/"),
        },
        "mailing_list": {
            "recipient": member,
            "sender": lead,
            "project": project,
            "subject": "Field week logistics",
            "body": "Hi all,\n\nThe boat leaves the station at 7:30 every morning "
            "from Monday. Bring your own dive logs and charge the cameras the "
            "night before.\n\nAlice",
        },
        "task_assigned": {
            "user": member,
            "assigned_by": lead,
            "project": project,
            "entries": [{"task": photos, "url": task_url(photos)}],
        },
        "comment_mention": {
            "user": member,
            "author_name": lead.name,
            "task": photos,
            "project": project,
            "excerpt": "@Jordan Lee can you check the white balance on frames "
            "40-60 before we upload?",
            "url": task_url(photos),
        },
        "digest": {
            "user": member,
            "frequency": "daily",
            "overdue_tasks": [counts, report],
            "notifications": notifications,
        },
        "announcement": {"announcement": announcement, "project": None},
        "overdue_reminder": {"user": member, "overdue_tasks": [counts, report]},
    }


def email_names() -> list[str]:
    return list(_samples())


def sample_context(name: str) -> dict:
    """The made-up context for ``name``; ``KeyError`` for an unknown email."""
    return _samples()[name]
