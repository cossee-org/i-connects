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


def test_no_template_uses_a_multi_line_django_comment():
    """`{# #}` is single-line only — a wrapped one renders as visible page text.

    Django never treats a `{# ... #}` that spans lines as a comment, so the
    developer note leaks into the UI instead. `{% comment %}` is the
    multi-line form. This scans every template rather than the few we know
    about, because the failure is silent and only shows up on the page.
    """
    from pathlib import Path

    import config

    templates = Path(config.__file__).resolve().parent.parent / "templates"
    offenders = []
    for path in sorted(templates.rglob("*.html")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # An opener with no closer on the same line never terminates.
            if line.count("{#") > line.count("#}"):
                rel = path.relative_to(templates)
                offenders.append(f"{rel}:{number}: {line.strip()[:70]}")

    assert not offenders, (
        "these {# #} comments span lines and will render as text; use "
        "{% comment %} instead:\n" + "\n".join(offenders)
    )
