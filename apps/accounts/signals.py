"""Signals for the accounts app.

Creates the default dashboard widget set when a new ``User`` is created,
mirroring the Prisma/Blitz signup side-effect.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.core.enums import WidgetSize

from .models import User, Widget

logger = logging.getLogger(__name__)


# (widget_type, size) -- ordered. ``position`` is the list index.
DEFAULT_WIDGETS: list[tuple[str, str]] = [
    ("last_project", WidgetSize.LARGE),
    ("recent_tasks", WidgetSize.LARGE),
    ("overdue_tasks", WidgetSize.LARGE),
    ("notifications", WidgetSize.LARGE),
    ("project_count", WidgetSize.SMALL),
    ("task_count", WidgetSize.SMALL),
    ("completed_count", WidgetSize.SMALL),
    ("pending_count", WidgetSize.SMALL),
    ("member_count", WidgetSize.SMALL),
    ("unread_count", WidgetSize.SMALL),
]


@receiver(post_save, sender=User)
def create_default_widgets(sender, instance, created, **kwargs):
    """Seed the default widget set on first User save."""
    if not created:
        return
    try:
        for idx, (widget_type, size) in enumerate(DEFAULT_WIDGETS):
            Widget.objects.get_or_create(
                user=instance,
                type=widget_type,
                defaults={"size": size, "position": idx, "show": True},
            )
    except Exception:  # pragma: no cover -- don't block signup on widget errors
        logger.exception("Failed to seed default widgets for user %s", instance.pk)
