"""Notifications app: user-facing alerts, tied optionally to a project."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.urls import NoReverseMatch, reverse


def is_safe_internal_path(link: str) -> bool:
    """Whether ``link`` is a path on this site rather than somewhere else.

    A single leading slash is this site; ``//evil.example`` is a
    protocol-relative URL pointing off it, and anything with a scheme
    (``https:``, and more to the point ``javascript:``) is not a path.
    """
    return link.startswith("/") and not link.startswith("//")


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

    @property
    def details(self) -> str:
        """Everything in ``message`` after the :attr:`headline`."""
        parts = self.message.split("\n", 1)
        return parts[1].strip() if len(parts) > 1 else ""

    @property
    def url(self) -> str | None:
        """Where clicking this notification should land, or ``None``.

        ``route_data`` holds the *route* a producer named — ``{"route":
        "tasks:detail", "kwargs": {...}}`` — rather than a URL string, so a
        notification written months ago still points at the right page after
        a URL pattern moves. A stored route that no longer reverses (the
        object was deleted, the pattern was renamed) falls through to the
        project rather than 500-ing on a page the user only wanted to read.

        Announcements instead carry a free-text ``link``; only an internal
        path is used for navigation here (see ``external_link``).
        """
        route = (self.route_data or {}).get("route")
        if route:
            try:
                return reverse(route, kwargs=(self.route_data or {}).get("kwargs") or None)
            except NoReverseMatch:
                pass

        link = (self.route_data or {}).get("link")
        if link and is_safe_internal_path(link):
            return link

        if self.project_id:
            try:
                return reverse("projects:detail", kwargs={"pk": self.project_id})
            except NoReverseMatch:
                pass
        return None

    @property
    def external_link(self) -> str | None:
        """An announcement's off-site link, if it has one.

        Kept apart from :attr:`url` so the click-through view only ever
        redirects to a path this app built itself — an admin-supplied URL
        reached through a redirect endpoint would be an open redirect.
        """
        link = (self.route_data or {}).get("link")
        if link and not is_safe_internal_path(link):
            return link
        return None
