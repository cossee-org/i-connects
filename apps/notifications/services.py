"""Public service helpers for the notifications app.

Other apps should import ``create_notification`` from here rather than
touching the ``Notification`` model directly.
"""

from __future__ import annotations

from typing import Iterable, Optional

from django.contrib.auth import get_user_model
from django.db import transaction

from .models import Notification


def _build_message(title: Optional[str], body: Optional[str]) -> str:
    title = (title or "").strip()
    body = (body or "").strip()
    if title and body:
        return f"{title}\n\n{body}"
    return title or body


@transaction.atomic
def create_notification(
    users: Iterable,
    title: str = "",
    body: str = "",
    project=None,
    link: Optional[str] = None,
    announcement: bool = False,
) -> Notification:
    """Create a :class:`Notification` and attach ``users`` as recipients.

    Parameters
    ----------
    users:
        Iterable of user instances or user IDs.
    title, body:
        The notification headline and body. Stored concatenated in
        ``Notification.message`` so the existing schema is preserved.
    project:
        Optional :class:`apps.projects.models.Project` that scoped the
        notification.
    link:
        Optional URL/route the UI should navigate to when the notification
        is clicked. Stored on ``route_data`` as ``{"link": ...}``.
    announcement:
        Whether this is an admin announcement (affects rendering).
    """
    message = _build_message(title, body)
    route_data = {"link": link} if link else None

    notification = Notification.objects.create(
        message=message,
        project=project,
        announcement=announcement,
        route_data=route_data,
    )

    user_ids = []
    User = get_user_model()
    for u in users or []:
        if u is None:
            continue
        if isinstance(u, User):
            user_ids.append(u.pk)
        else:
            # Assume it's already a primary key.
            user_ids.append(int(u))
    if user_ids:
        notification.recipients.add(*user_ids)
    return notification
