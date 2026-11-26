"""Idempotently install the i-CONNECTS periodic tasks into django-celery-beat.

Run once per deploy (or whenever the schedule changes)::

    python manage.py setup_cron
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django_celery_beat.models import CrontabSchedule, PeriodicTask


# (name, task dotted path, crontab kwargs)
SCHEDULE: tuple[tuple[str, str, dict], ...] = (
    (
        "daily_digest",
        "cron.tasks.daily_digest_cron",
        {"minute": "0", "hour": "6", "day_of_week": "*", "day_of_month": "*", "month_of_year": "*"},
    ),
    (
        "weekly_digest",
        "cron.tasks.weekly_digest_cron",
        {"minute": "0", "hour": "6", "day_of_week": "1", "day_of_month": "*", "month_of_year": "*"},
    ),
    (
        "overdue_task_reminder",
        "cron.tasks.overdue_task_reminder_cron",
        {"minute": "0", "hour": "9", "day_of_week": "*", "day_of_month": "*", "month_of_year": "*"},
    ),
    (
        "zulip_deadline_reminder",
        "cron.tasks.zulip_deadline_reminder_cron",
        {"minute": "0", "hour": "8", "day_of_week": "*", "day_of_month": "*", "month_of_year": "*"},
    ),
    (
        "cleanup_tmp_files",
        "cron.tasks.cleanup_tmp_files_cron",
        {"minute": "0", "hour": "2", "day_of_week": "*", "day_of_month": "*", "month_of_year": "*"},
    ),
)


class Command(BaseCommand):
    help = "Install/refresh django-celery-beat periodic task rows for i-CONNECTS cron jobs."

    def handle(self, *args, **options):
        for name, task, crontab_kwargs in SCHEDULE:
            schedule, _ = CrontabSchedule.objects.get_or_create(**crontab_kwargs)
            periodic, created = PeriodicTask.objects.update_or_create(
                name=name,
                defaults={
                    "task": task,
                    "crontab": schedule,
                    "interval": None,
                    "enabled": True,
                },
            )
            verb = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{verb} periodic task: {name} -> {task}"))
