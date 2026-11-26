"""Outbound Zulip helpers: post a project's activity to its team chat.

A project is connected through a :class:`~apps.integrations.models.ZulipConnection`
— a Zulip organisation, a bot, a channel and a topic. Messages go through
Zulip's own ``POST /api/v1/messages`` endpoint with the bot's credentials,
using ``urllib.request`` from the standard library (no runtime dependency),
the same way :mod:`apps.integrations.discord` does.

Three layers, lowest first:

- :func:`deliver` makes the HTTP call and reports what happened. It is the
  only function that touches the network.
- :func:`announce` is what the rest of the app calls: given a project, a
  notification group and a message, it queues the delivery for after the
  current transaction commits. It never raises and never blocks a request
  on Zulip being slow.
- The ``*_message`` builders turn app objects into Zulip markdown.

Security: the organisation's host is checked against an allowlist
(``ZULIP_ALLOWED_HOST_SUFFIXES``, Zulip Cloud by default) before anything
is sent, so a Lead cannot point the server at an internal address. Text
that came from a user is passed through :func:`plain` before it reaches a
message, so a task called ``@**all**`` pings nobody.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterable, Optional

from django.conf import settings
from django.db import transaction
from django.urls import reverse

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_HOST_SUFFIXES = ("zulipchat.com",)

# Notification groups — each is a ``notify_<group>`` flag on the connection.
TASKS = "tasks"
MEMBERS = "members"
COMMENTS = "comments"
WIKI = "wiki"
DEADLINES = "deadlines"

# Zulip rejects messages over 10 000 characters; stay well inside it.
MAX_CONTENT_LENGTH = 6000


# ---------------------------------------------------------------------------
# Validation and URLs
# ---------------------------------------------------------------------------
def allowed_host_suffixes() -> tuple[str, ...]:
    return tuple(
        getattr(settings, "ZULIP_ALLOWED_HOST_SUFFIXES", None)
        or DEFAULT_ALLOWED_HOST_SUFFIXES
    )


def is_valid_site(url: str) -> bool:
    """``https`` only, and the host must be (a subdomain of) an allowed one."""
    if not url:
        return False
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return any(
        host == suffix or host.endswith("." + suffix)
        for suffix in allowed_host_suffixes()
    )


def normalise_site(url: str) -> str:
    """Reduce whatever was pasted to ``https://host`` (no path, no slash)."""
    parsed = urllib.parse.urlparse((url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return (url or "").strip()
    return f"{parsed.scheme}://{parsed.netloc}"


def _hash_component(value: str) -> str:
    """Encode a channel or topic name the way Zulip's web app links do."""
    return (
        urllib.parse.quote(value, safe="")
        .replace(".", "%2E")
        .replace("%", ".")
    )


def topic_url(connection) -> str:
    """Link that opens the connected topic in Zulip."""
    return (
        f"{connection.site}/#narrow/channel/{_hash_component(connection.channel)}"
        f"/topic/{_hash_component(connection.topic)}"
    )


def absolute_url(route: str, **kwargs) -> str:
    """Absolute i-CONNECTS URL for a route, or ``""`` without ``SITE_URL``.

    Messages are read outside the app, so a relative link is no use; when
    the site's address is not configured the builders leave the link out.
    """
    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    if not base:
        return ""
    return base + reverse(route, kwargs=kwargs)


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------
def plain(text, *, limit: int = 120) -> str:
    """Make user-entered text safe to drop into a line of Zulip markdown.

    Collapses it to one line, defuses mentions (a zero-width space after
    every ``@``, so ``@**all**`` is just text), swaps the characters that
    would break a link label or open a code span, and truncates.
    """
    text = " ".join(str(text or "").split())
    text = (
        text.replace("@", "@​")
        .replace("[", "(")
        .replace("]", ")")
        .replace("`", "'")
    )
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def quote(text, *, limit: int = 500) -> str:
    """User-entered text as a Zulip quote block, line breaks kept."""
    text = str(text or "").strip().replace("@", "@​")
    # A fence inside the text would close the quote block early.
    text = text.replace("```", "'''").replace("~~~", "---")
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return f"```quote\n{text}\n```"


def link(label: str, url: str) -> str:
    """A markdown link, or the bold label when there is nowhere to link."""
    return f"[{label}]({url})" if url else f"**{label}**"


def user_name(user) -> str:
    """A person's name for a message. Never their email address: the chat
    may be read by people who are not on the project."""
    if user is None:
        return "Someone"
    full = " ".join(
        part
        for part in (
            (getattr(user, "given_name", "") or "").strip(),
            (getattr(user, "family_name", "") or "").strip(),
        )
        if part
    )
    return plain(full or getattr(user, "name", "") or "Someone", limit=80)


def member_name(member) -> str:
    """A ``ProjectMember``'s name: the team's, or its user's."""
    if member is None:
        return "Someone"
    if member.name:
        return plain(member.name, limit=80)
    return user_name(member.users.order_by("given_name", "family_name").first())


def member_names(members: Iterable) -> str:
    return ", ".join(member_name(m) for m in members)


def task_link(task) -> str:
    return link(
        plain(task.name),
        absolute_url("tasks:detail", project_pk=task.project_id, pk=task.pk),
    )


def due(deadline) -> str:
    """A deadline as ``12 Nov 2026``, in the site's time zone."""
    from django.utils import timezone

    return timezone.localtime(deadline).strftime("%-d %b %Y")


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------
def deliver(connection, content: str, *, timeout: float = 8.0) -> tuple[bool, str]:
    """Send ``content`` to the connection's topic, now.

    Returns ``(ok, detail)``: on failure ``detail`` is a sentence fit to
    show the Lead who is setting the connection up. Never raises.
    """
    if not is_valid_site(connection.site):
        logger.warning("zulip message skipped: site not allowed")
        return False, "That Zulip address is not allowed."
    token = base64.b64encode(
        f"{connection.bot_email}:{connection.api_key}".encode("utf-8")
    ).decode("ascii")
    data = urllib.parse.urlencode(
        {
            "type": "stream",
            "to": connection.channel,
            "topic": connection.topic,
            "content": content[:MAX_CONTENT_LENGTH],
        }
    ).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{connection.site}/api/v1/messages",
            data=data,
            headers={
                "Authorization": f"Basic {token}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "i-CONNECTS/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                return True, ""
            return False, f"Zulip answered with status {resp.status}."
    except urllib.error.HTTPError as exc:
        # Zulip explains a refusal in the body: {"result": "error", "msg": ...}
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("msg", "")
        except Exception:  # noqa: BLE001
            pass
        logger.warning("zulip message refused: %s %s", exc.code, detail)
        if exc.code == 401:
            return False, "Zulip did not accept the bot's email and API key."
        return False, detail or f"Zulip answered with status {exc.code}."
    except (urllib.error.URLError, TimeoutError) as exc:
        logger.warning("zulip message failed: %s", exc)
        return False, "Could not reach Zulip."
    except Exception as exc:  # noqa: BLE001 — a chat message must never break the app.
        logger.warning("zulip message unexpected error: %s", exc)
        return False, "Could not reach Zulip."


def connection_for(project, group: Optional[str] = None):
    """The project's active connection, or ``None``.

    With ``group``, also ``None`` when that kind of message is switched off.
    """
    from .models import ZulipConnection

    if project is None:
        return None
    connection = ZulipConnection.objects.filter(project=project, enabled=True).first()
    if connection is None:
        return None
    if group is not None and not getattr(connection, f"notify_{group}", False):
        return None
    return connection


def announce(project, group: str, content: str) -> bool:
    """Queue ``content`` for the project's Zulip topic. Best-effort.

    Returns whether a message was queued (the project is connected and this
    group is on). The send itself happens in a Celery task once the current
    transaction commits — a rolled-back change announces nothing, and Zulip
    being slow or down costs the request nothing.
    """
    if not content:
        return False
    try:
        connection = connection_for(project, group)
        if connection is None:
            return False
        from .tasks import post_zulip_message

        connection_id = connection.pk

        # A named function, not ``functools.partial``: when a robust
        # callback fails Django logs its ``__qualname__``, which a partial
        # does not have — the log line itself would raise.
        def send_to_zulip() -> None:
            post_zulip_message.delay(connection_id, content)

        # robust: a broker that is down must not fail the change announced.
        transaction.on_commit(send_to_zulip, robust=True)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("zulip announce failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
def task_created_message(task, *, actor) -> str:
    parts = [f"**{user_name(actor)}** created the task {task_link(task)}"]
    assignees = list(task.assigned_members.all())
    if assignees:
        parts.append(f"assigned to {member_names(assignees)}")
    if task.deadline:
        parts.append(f"due {due(task.deadline)}")
    return " · ".join(parts)


def task_assigned_message(task, members: Iterable) -> str:
    text = f"{task_link(task)} was assigned to {member_names(members)}"
    if task.deadline:
        text += f" · due {due(task.deadline)}"
    return text


def task_part_done_message(task, *, member) -> str:
    return f"**{member_name(member)}** marked their part of {task_link(task)} as done"


def task_reviewed_message(task, *, actor, assignee) -> str:
    return (
        f"**{user_name(actor)}** reviewed {member_name(assignee)}'s part of "
        f"{task_link(task)}"
    )


def task_completed_message(task, *, actor) -> str:
    text = f"The task {task_link(task)} is complete"
    if actor is not None:
        text += f" (closed by {user_name(actor)})"
    return text


def task_approved_message(task, *, actor) -> str:
    return f"**{user_name(actor)}** approved the work on {task_link(task)}"


def member_joined_message(project, *, user) -> str:
    return (
        f"**{user_name(user)}** joined "
        f"{link(plain(project.name), absolute_url('projects:detail', pk=project.pk))}"
    )


def comment_message(comment, task) -> str:
    return (
        f"**{member_name(comment.author)}** commented on {task_link(task)}:\n"
        f"{quote(comment.content)}"
    )


def wiki_message(page, *, actor, verb: str) -> str:
    url = absolute_url("wiki:detail", project_pk=page.project_id, slug=page.slug)
    return f"**{user_name(actor)}** {verb} the wiki page {link(plain(page.title), url)}"


def team_message(*, user, text: str) -> str:
    """A message a member typed in i-CONNECTS for the team chat."""
    return f"**{user_name(user)}** (via i-CONNECTS):\n{quote(text, limit=4000)}"


def connected_message(project) -> str:
    return (
        "i-CONNECTS is now connected to this topic for the project "
        f"{link(plain(project.name), absolute_url('projects:detail', pk=project.pk))}. "
        "Project activity will be posted here."
    )


def deadlines_message(*, due_soon: list, overdue: list) -> str:
    """The daily deadline reminder for one project, or ``""`` for nothing."""

    def line(task) -> str:
        text = f"- {task_link(task)} · {due(task.deadline)}"
        assignees = list(task.assigned_members.all())
        if assignees:
            text += f" · {member_names(assignees)}"
        return text

    blocks = []
    if due_soon:
        blocks.append(
            "**Deadlines coming up**\n" + "\n".join(line(t) for t in due_soon)
        )
    if overdue:
        blocks.append(
            "**Now overdue**\n" + "\n".join(line(t) for t in overdue)
        )
    return "\n\n".join(blocks)
