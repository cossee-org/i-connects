"""ContributionRecord: the ledger of credit earned on closed tasks.

A record is written when a project lead closes a task on a project that
tracks contributions as CRediT, one per user behind each assignee who
marked their own part done (and had it reviewed, where the task carries
reviewers). Reopening the task withdraws them again.

The rows follow task state rather than standing apart from it, so every FK
cascades: deleting a task, a project or a user takes the credit recorded
against it. Leads can also correct a record by hand — credit is a claim
about who did what, and a wrong claim has to be fixable.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.enums import ContributionType


class ContributionRecord(models.Model):
    task = models.ForeignKey(
        "tasks.Task",
        on_delete=models.CASCADE,
        related_name="contributions",
    )
    task_log = models.ForeignKey(
        "tasks.TaskLog",
        on_delete=models.CASCADE,
        related_name="contributions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="contributions",
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
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
    updated_at = models.DateTimeField(auto_now=True)

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
