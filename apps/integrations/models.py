"""Models for outbound integrations.

Only Zulip has a model: its connection needs more than the single URL a
Discord webhook is, and keeping it off ``Project`` leaves that table alone.
"""

from __future__ import annotations

from django.db import models


class ZulipConnection(models.Model):
    """Where a project's activity is posted on Zulip, and as which bot.

    One row per project. The bot's API key is a secret: it is stored as
    entered (like ``Project.discord_webhook_url``), never rendered back into
    a form, and only ever sent to ``site``.
    """

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    project = models.OneToOneField(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="zulip_connection",
    )

    site = models.URLField(
        max_length=255,
        help_text="Your Zulip organisation, e.g. https://example.zulipchat.com",
    )
    bot_email = models.EmailField(
        max_length=255,
        help_text="The bot's email address, shown on the bot's card in Zulip.",
    )
    api_key = models.CharField(max_length=255)
    # Zulip caps channel and topic names at 60 characters.
    channel = models.CharField(max_length=60)
    topic = models.CharField(max_length=60)

    enabled = models.BooleanField(
        default=True,
        help_text="Turn off to pause every message without losing the settings.",
    )

    # What gets announced. Each group is one checkbox on the settings form.
    notify_tasks = models.BooleanField(default=True)
    notify_members = models.BooleanField(default=True)
    notify_comments = models.BooleanField(default=True)
    notify_wiki = models.BooleanField(default=True)
    notify_deadlines = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Zulip connection"

    def __str__(self) -> str:
        return f"{self.project} -> {self.channel} > {self.topic}"
