"""Roles app: user-authored role definitions that can be attached to project members."""

from __future__ import annotations

from django.conf import settings
from django.db import models


class Role(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="roles",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    taxonomy = models.CharField(max_length=255, null=True, blank=True)

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="roles",
    )

    # Reverse many-to-many relations are declared on ProjectMember, Task, and
    # Invitation models -- Django will surface them as ``project_members``,
    # ``tasks``, and ``invitations``.

    project_members = models.ManyToManyField(
        "projects.ProjectMember",
        blank=True,
        related_name="roles",
    )

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["project"]),
        ]

    def __str__(self) -> str:
        return self.name
