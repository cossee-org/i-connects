"""Comments app: Comment and per-member CommentReadStatus."""

from __future__ import annotations

from django.db import models


class Comment(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    content = models.TextField()

    task = models.ForeignKey(
        "tasks.Task",
        on_delete=models.CASCADE,
        related_name="comments",
        null=True,
        blank=True,
    )
    task_log = models.ForeignKey(
        "tasks.TaskLog",
        on_delete=models.CASCADE,
        related_name="comments",
        null=True,
        blank=True,
    )
    author = models.ForeignKey(
        "projects.ProjectMember",
        on_delete=models.CASCADE,
        related_name="comments",
    )

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["task"]),
            models.Index(fields=["task_log"]),
            models.Index(fields=["author"]),
        ]

    def __str__(self) -> str:
        return f"Comment #{self.pk} by {self.author_id}"


class CommentReadStatus(models.Model):
    """Tracks whether a given project member has read a given comment."""

    created_at = models.DateTimeField(auto_now_add=True)
    comment = models.ForeignKey(
        Comment,
        on_delete=models.CASCADE,
        related_name="read_statuses",
    )
    project_member = models.ForeignKey(
        "projects.ProjectMember",
        on_delete=models.CASCADE,
        related_name="comment_read_statuses",
    )
    read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [["comment", "project_member"]]
        indexes = [
            models.Index(fields=["comment"]),
            models.Index(fields=["project_member"]),
        ]

    def __str__(self) -> str:
        return f"read={self.read} for comment {self.comment_id} by {self.project_member_id}"
