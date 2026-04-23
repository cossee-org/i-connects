"""Mailing list app: per-project announcement emails with an on-site archive.

A ``MailingListMessage`` row is the persistent archive copy of a message that
a project manager sent, by email, to every member of the project. It lets
future members (who joined after the send) read the history.

Note: the model is **not** append-only. A PM may delete an archive entry
that was sent in error. The email itself cannot be recalled once it has
left our outbound mail queue — ``delete`` only removes the on-site record.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class MailingListMessage(models.Model):
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="mailing_list_messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="mailing_list_messages",
    )
    subject = models.CharField(max_length=255)
    # Plain text for v1. If rich formatting is needed later, add a separate
    # ``body_html`` column rather than overloading this one.
    body = models.TextField()
    sent_at = models.DateTimeField(auto_now_add=True)
    recipient_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-sent_at", "-id"]
        indexes = [models.Index(fields=["project", "-sent_at"])]

    def __str__(self) -> str:
        return f"[{self.project_id}] {self.subject}"
