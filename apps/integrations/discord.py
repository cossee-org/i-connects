"""Outbound Discord webhook helpers.

Best-effort announcements posted to a project's configured Discord webhook
when a task is approved. Uses ``urllib.request`` from the standard library
to avoid adding a runtime dependency.

Security: an explicit host allowlist (``discord.com`` / ``discordapp.com``
only) protects against SSRF via user-supplied webhook URLs — without it,
an attacker with edit rights on a project could point the URL at an
internal service and have the server fetch it.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

ALLOWED_HOST_SUFFIXES = ("discord.com", "discordapp.com")


def is_valid_webhook_url(url: str) -> bool:
    """Narrow validation: scheme must be https and host must end in an allowed suffix."""
    if not url:
        return False
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = parsed.hostname or ""
    return any(host == s or host.endswith("." + s) for s in ALLOWED_HOST_SUFFIXES)


def post_webhook(url: str, *, payload: dict, timeout: float = 5.0) -> Optional[int]:
    """POST ``payload`` as JSON to ``url``. Best-effort; returns HTTP status
    code or None on failure. Never raises. Logs on error."""
    if not is_valid_webhook_url(url):
        logger.warning("discord webhook skipped: invalid URL")
        return None
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "i-CONNECTS/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except (urllib.error.URLError, TimeoutError) as exc:
        logger.warning("discord webhook failed: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001 — webhook failure must not propagate.
        logger.warning("discord webhook unexpected error: %s", exc)
        return None


def announce_task_approved(task, *, verifier) -> Optional[int]:
    """Post a Discord embed for a just-approved task. No-op if the project
    has no webhook URL configured."""
    webhook = (task.project.discord_webhook_url or "").strip()
    if not webhook:
        return None
    type_label = task.get_contribution_type_display()
    embed = {
        "title": f"Task approved: {task.name}",
        "color": 0x323895,  # i-CONNECTS navy
        "fields": [
            {"name": "Project", "value": task.project.name, "inline": True},
            {"name": "Type", "value": type_label, "inline": True},
            {
                "name": "Approved by",
                "value": getattr(verifier, "name", None)
                or getattr(verifier, "email", "")
                or "—",
                "inline": True,
            },
        ],
        "footer": {"text": "i-CONNECTS"},
    }
    return post_webhook(webhook, payload={"embeds": [embed]})
