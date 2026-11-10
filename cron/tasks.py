"""Scheduled (beat-driven) Celery tasks for i-connects.

These are fan-out / maintenance jobs. Each one is thin: it iterates the
relevant queryset and enqueues per-user work through ``.delay()`` so slow
email sends don't block the beat schedule.

Schedules are configured by ``apps/core/management/commands/setup_cron.py``
which writes rows into django-celery-beat.
"""

from __future__ import annotations

import logging
import shutil
from datetime import timedelta
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.core.enums import EmailFrequency, TaskStatus

logger = logging.getLogger(__name__)


def _users_with_frequency(field: str, frequency: str):
    User = get_user_model()
    return User.objects.filter(**{field: frequency, "is_active": True})


@shared_task(name="cron.daily_digest")
def daily_digest_cron() -> int:
    """Fan out :func:`send_digest_email_task` to every daily-digest user."""
    from apps.notifications.tasks import send_digest_email_task

    users = _users_with_frequency(
        "email_project_activity_frequency", EmailFrequency.DAILY
    )
    count = 0
    for user in users.only("id"):
        send_digest_email_task.delay(user.id, "daily")
        count += 1
    logger.info("daily_digest_cron: enqueued %s users", count)
    return count


@shared_task(name="cron.weekly_digest")
def weekly_digest_cron() -> int:
    """Fan out :func:`send_digest_email_task` to every weekly-digest user."""
    from apps.notifications.tasks import send_digest_email_task

    users = _users_with_frequency(
        "email_project_activity_frequency", EmailFrequency.WEEKLY
    )
    count = 0
    for user in users.only("id"):
        send_digest_email_task.delay(user.id, "weekly")
        count += 1
    logger.info("weekly_digest_cron: enqueued %s users", count)
    return count


@shared_task(name="cron.overdue_task_reminder")
def overdue_task_reminder_cron() -> int:
    """Email reminders for overdue tasks.

    Finds all ``NOT_COMPLETED`` tasks with a deadline in the past, collects
    the set of assigned users (via ``assigned_members.users``), and enqueues
    a digest-style email for each user whose ``email_overdue_task_frequency``
    is not ``NEVER``. The digest task does the actual rendering/sending.
    """
    from apps.notifications.tasks import send_digest_email_task
    from apps.tasks.models import Task

    User = get_user_model()
    now = timezone.now()

    overdue = Task.objects.filter(
        deadline__lt=now,
        status=TaskStatus.NOT_COMPLETED,
    )
    user_ids = (
        User.objects.filter(
            project_memberships__assigned_tasks__in=overdue,
        )
        .exclude(email_overdue_task_frequency=EmailFrequency.NEVER)
        .filter(is_active=True)
        .values_list("id", flat=True)
        .distinct()
    )

    count = 0
    for uid in user_ids:
        # Reuse the digest task — it already filters for this user's overdue
        # tasks and will no-op if none are visible.
        send_digest_email_task.delay(uid, "daily")
        count += 1
    logger.info("overdue_task_reminder_cron: enqueued %s users", count)
    return count


@shared_task(name="cron.cleanup_tmp_files")
def cleanup_tmp_files_cron(max_age_hours: int = 24) -> int:
    """Delete files under ``MEDIA_ROOT/tmp/`` older than ``max_age_hours``."""
    tmp_root = Path(settings.MEDIA_ROOT) / "tmp"
    if not tmp_root.exists():
        return 0

    cutoff = timezone.now().timestamp() - (max_age_hours * 3600)
    removed = 0
    for entry in tmp_root.iterdir():
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            logger.warning("cleanup_tmp_files_cron: could not remove %s: %s", entry, exc)
    logger.info("cleanup_tmp_files_cron: removed %s entries", removed)
    return removed


@shared_task(name="cron.tasks.zulip_deadline_reminder_cron")
def zulip_deadline_reminder_cron() -> int:
    """Post each connected project's upcoming deadlines to its Zulip topic.

    One message per project, once a day: the open tasks due within
    ``ZULIP_DEADLINE_WINDOW_DAYS`` and the ones that went overdue since the
    last run. A task is listed as overdue once, then left alone — the topic
    is for the team to talk in, not a wall of the same reminders. Projects
    with nothing to say get no message.
    """
    from apps.integrations import zulip
    from apps.integrations.models import ZulipConnection
    from apps.integrations.tasks import post_zulip_message
    from apps.tasks.models import Task

    now = timezone.now()
    window = timedelta(days=getattr(settings, "ZULIP_DEADLINE_WINDOW_DAYS", 3))

    count = 0
    connections = ZulipConnection.objects.filter(
        enabled=True, notify_deadlines=True
    ).select_related("project")
    for connection in connections:
        open_tasks = (
            Task.objects.filter(
                project=connection.project, status=TaskStatus.NOT_COMPLETED
            )
            .prefetch_related("assigned_members__users")
            .order_by("deadline")
        )
        content = zulip.deadlines_message(
            due_soon=list(open_tasks.filter(deadline__gte=now, deadline__lt=now + window)),
            overdue=list(
                open_tasks.filter(deadline__gte=now - timedelta(days=1), deadline__lt=now)
            ),
        )
        if content:
            post_zulip_message.delay(connection.pk, content)
            count += 1
    logger.info("zulip_deadline_reminder_cron: posted for %s projects", count)
    return count
