"""Template context for the notification chrome in ``base.html``.

The sidebar link and the top-bar bell both render an unread count on the
first paint, before HTMX has had a chance to poll ``notifications:badge``.
"""

from __future__ import annotations


def unread_notifications(request):
    if not request.user.is_authenticated:
        return {}

    from .models import Notification

    return {
        "unread_notifications_count": Notification.objects.unread_for_user(
            request.user
        ).count(),
    }
