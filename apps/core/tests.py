"""Smoke tests for shared cron tasks + management commands."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django_celery_beat.models import PeriodicTask

from cron.tasks import daily_digest_cron


@pytest.mark.django_db
def test_daily_digest_cron_runs_without_error():
    # With 0 users matching the DAILY email frequency, the task should
    # simply return 0 without raising.
    result = daily_digest_cron()
    assert result == 0


@pytest.mark.django_db
def test_setup_cron_command_creates_periodic_tasks():
    call_command("setup_cron")
    # The 4 scheduled jobs registered by SCHEDULE in setup_cron.py.
    expected_names = {
        "daily_digest",
        "weekly_digest",
        "overdue_task_reminder",
        "cleanup_tmp_files",
    }
    names = set(PeriodicTask.objects.values_list("name", flat=True))
    assert expected_names.issubset(names)
    assert PeriodicTask.objects.filter(name__in=expected_names).count() == 4
