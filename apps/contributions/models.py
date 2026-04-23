"""ContributionRecord: an append-only ledger of task-close events.

Per Ultra Plan §8:

    Contribution records are immutable event logs, not editable ratings. A
    contribution is created when a task is closed. It cannot be edited or
    deleted by anyone (including admin).

The model therefore:
- has no ``updated_at`` field;
- overrides ``save()`` to refuse updates to an existing row;
- overrides ``delete()`` to refuse deletions outright;
- uses ``on_delete=PROTECT`` on every FK so the row cannot be orphaned by
  deleting its task / user / project (soft-delete is the correct path for
  those — §7).
"""

from __future__ import annotations

from django.conf import settings
from django.db import IntegrityError, models

from apps.core.enums import ContributionType


class ContributionRecord(models.Model):
    task = models.ForeignKey(
        "tasks.Task",
        on_delete=models.PROTECT,
        related_name="contributions",
    )
    task_log = models.ForeignKey(
        "tasks.TaskLog",
        on_delete=models.PROTECT,
        related_name="contributions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="contributions",
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.PROTECT,
        related_name="contributions",
    )
    contribution_type = models.CharField(
        max_length=32,
        choices=ContributionType.choices,
        default=ContributionType.OTHER,
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="contributions_verified",
    )
    closed_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-closed_at", "-id"]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["project"]),
            models.Index(fields=["user", "project"]),
            models.Index(fields=["contribution_type"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["task_log", "user"],
                name="contribution_unique_per_tasklog_user",
            ),
        ]

    def __str__(self) -> str:
        return f"Contribution #{self.pk} ({self.user_id} / {self.contribution_type})"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise IntegrityError(
                "ContributionRecord is append-only; existing rows cannot be updated."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise IntegrityError(
            "ContributionRecord is append-only; rows cannot be deleted."
        )
