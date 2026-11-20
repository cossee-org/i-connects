"""Models for the communications module.

``EmailLog`` is the record of an email leaving i-connects: one row per
message per recipient, written at the moment of sending by
:func:`apps.communications.services.send_logged`. It holds what a Lead needs
to answer "did this person get an email, and when" — who, when, what kind,
the subject line and whether the send went through — and deliberately not the
body, which for some emails is personal to the recipient.

"Sent" means the mail backend accepted the message. Whether it was then
delivered, bounced or filed as spam is known only to the mail provider.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class EmailKind(models.TextChoices):
    INVITATION = "invitation", "Invitation"
    JOIN_REQUEST = "join_request", "Join request"
    JOIN_REQUEST_DECISION = "join_request_decision", "Join request decision"
    TASK_ASSIGNED = "task_assigned", "Task assigned"
    MAILING_LIST = "mailing_list", "Mailing list message"
    ANNOUNCEMENT = "announcement", "Announcement"
    DIGEST = "digest", "Activity digest"


class EmailStatus(models.TextChoices):
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"


class EmailLog(models.Model):
    sent_at = models.DateTimeField(auto_now_add=True)
    kind = models.CharField(max_length=32, choices=EmailKind.choices)
    # The address the message went to, as it was at the time. Kept apart from
    # ``recipient`` because an invitation can go to an address with no
    # account yet, and because an account's address can change later.
    to_email = models.EmailField(max_length=254)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="email_logs",
    )
    # Null for an email that is not about one project (a digest spans every
    # project its reader is in; an announcement can be site-wide).
    project = models.ForeignKey(
        "projects.Project",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="email_logs",
    )
    subject = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=16, choices=EmailStatus.choices, default=EmailStatus.SENT
    )
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-sent_at", "-id"]
        indexes = [
            models.Index(fields=["project", "-sent_at"]),
            models.Index(fields=["recipient", "-sent_at"]),
            models.Index(fields=["to_email"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} to {self.to_email} ({self.status})"
