"""Notifications app: user-facing alerts, tied optionally to a project."""

from __future__ import annotations

from django.conf import settings
from django.db import models


class NotificationQuerySet(models.QuerySet):
    """Custom queryset with the public ``for_user`` filter."""

    def for_user(self, user):
        """Return notifications whose M2M recipients include ``user``."""
        if not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(recipients=user)

    def unread_for_user(self, user):
        return self.for_user(user).filter(read=False)


class Notification(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    recipients = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="notifications",
        blank=True,
    )
    message = models.TextField()
    read = models.BooleanField(default=False)
    announcement = models.BooleanField(default=False)

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
    )
    route_data = models.JSONField(null=True, blank=True)

    objects = NotificationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project"]),
            models.Index(fields=["read"]),
        ]

    def __str__(self) -> str:
        return self.message[:60]

    @property
    def headline(self) -> str:
        """The first line of ``message``.

        ``services.create_notification`` stores the title and body joined by
        a blank line, so compact renderings (the top-bar dropdown) can show
        the title alone rather than truncating the two run together.
        """
        return self.message.split("\n", 1)[0]
