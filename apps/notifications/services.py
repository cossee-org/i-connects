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
    route: Optional[str] = None,
    route_kwargs: Optional[dict] = None,
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
        Optional literal URL, for announcements where an admin typed one.
        Stored on ``route_data`` as ``{"link": ...}``.
    announcement:
        Whether this is an admin announcement (affects rendering).
    route, route_kwargs:
        The view this notification is about — a URL name and its kwargs,
        e.g. ``route="tasks:detail", route_kwargs={"project_pk": 1,
        "pk": 2}``. Preferred over ``link`` for anything the app raises
        itself: the name is reversed when the notification is rendered, so
        it survives a URL pattern being moved. Callers pass the route
        rather than the object because notifications is imported *by*
        projects and tasks, not the other way round.
    """
    message = _build_message(title, body)

    route_data = None
    if route:
        route_data = {"route": route}
        if route_kwargs:
            route_data["kwargs"] = route_kwargs
    elif link:
        route_data = {"link": link}

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
