"""Models for the communications module.

``EmailLog`` is the record of an email leaving i-CONNECTS: one row per
message per recipient, written at the moment of sending by
:func:`apps.communications.services.send_logged`. It holds what a Lead needs
to answer "did this person get an email, and when" — who, when, what kind,
the subject line and whether the send went through — and, for an email about
a project, the email itself (sender, reply-to, plain-text and HTML bodies),
so a Lead can open exactly what the member was sent. Only the project's Leads
can read a body: it is shown on the email log and nowhere else, not even in
the Django admin. An email about no project (a digest, a site-wide
announcement) is in no project's log, so its body is not kept at all.

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
    REVIEW_ASSIGNED = "review_assigned", "Review assigned"
    COMMENT_MENTION = "comment_mention", "Tagged in a comment"
    MAILING_LIST = "mailing_list", "Mailing list message"
    ANNOUNCEMENT = "announcement", "Announcement"
    DIGEST = "digest", "Activity digest"


# Kinds i-CONNECTS sends on its own, with no member behind them.
AUTOMATIC_KINDS = frozenset({EmailKind.DIGEST})


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
    # The member whose action sent the email: the Lead who invited, assigned
    # or decided, the requester, the comment's author, the mailing list
    # message's sender. Null for an email i-CONNECTS sends on its own (a
    # digest), and for rows written before this was recorded.
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="triggered_email_logs",
    )
    subject = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=16, choices=EmailStatus.choices, default=EmailStatus.SENT
    )
    error = models.TextField(blank=True, default="")
    from_email = models.CharField(max_length=255, blank=True, default="")
    reply_to = models.CharField(max_length=255, blank=True, default="")
    # The email as the recipient got it. Empty for an email about no project,
    # and for rows written before bodies were kept.
    text_body = models.TextField(blank=True, default="")
    html_body = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-sent_at", "-id"]
        indexes = [
            models.Index(fields=["project", "-sent_at"]),
            models.Index(fields=["recipient", "-sent_at"]),
            models.Index(fields=["to_email"]),
        ]

    @property
    def is_automatic(self) -> bool:
        return self.kind in AUTOMATIC_KINDS

    @property
    def has_body(self) -> bool:
        return bool(self.text_body or self.html_body)

    def __str__(self) -> str:
        return f"{self.get_kind_display()} to {self.to_email} ({self.status})"
