"""Smoke tests for shared cron tasks + management commands."""

from __future__ import annotations

import pytest
from django.core.exceptions import MiddlewareNotUsed
from django.core.management import call_command
from django.http import HttpResponse
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


def test_every_sizing_class_a_template_uses_exists_in_the_built_css():
    """A `w-`/`h-` utility no build has generated silently collapses the box.

    Tailwind only emits the utilities it finds in the content it scans, so a
    template that reaches for a size no other template uses depends on
    `npm run build:css` having been run since. Where it hasn't — a cached
    stylesheet, a checkout that skipped the step — the element gets no width
    or height and shrinks to fit its content: a `rounded-full` avatar stops
    being a circle. Nothing errors, so only the page shows it.
    """
    import re
    from pathlib import Path

    import config

    root = Path(config.__file__).resolve().parent.parent
    css = (root / "static" / "css" / "output.css").read_text(encoding="utf-8")

    offenders = []
    for path in sorted((root / "templates").rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            for name in re.findall(r"(?<![\w-])([wh]-\d+(?:\.\d+)?)(?![\w.-])", line):
                # Tailwind escapes the dot in fractional names like ``w-3.5``.
                selector = "." + name.replace(".", "\\.") + "{"
                if selector not in css:
                    rel = path.relative_to(root / "templates")
                    offenders.append(f"{rel}:{number}: {name}")

    assert not offenders, (
        "these sizing classes are used in templates but absent from "
        "static/css/output.css — run `npm run build:css` and commit the "
        "result, or use a size the app already uses:\n" + "\n".join(offenders)
    )


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


# ---------------------------------------------------------------------------
# UnappliedMigrationsMiddleware
# ---------------------------------------------------------------------------


def _middleware(pending, *, calls=None):
    """The middleware with its migration check stubbed to return ``pending``.

    ``calls`` collects one entry per check, so a test can show the second
    request did not go back to the database.
    """
    from django.test import override_settings

    from apps.core.middleware import UnappliedMigrationsMiddleware

    with override_settings(DEBUG=True):
        middleware = UnappliedMigrationsMiddleware(lambda request: HttpResponse("view"))
    def _pending():
        if calls is not None:
            calls.append(1)
        return pending
    middleware._pending = staticmethod(_pending)
    return middleware


def test_middleware_is_dropped_when_debug_is_off():
    """A deployment pays nothing for it."""
    from django.test import override_settings

    from apps.core.middleware import UnappliedMigrationsMiddleware

    with override_settings(DEBUG=False):
        with pytest.raises(MiddlewareNotUsed):
            UnappliedMigrationsMiddleware(lambda request: None)


def test_a_pending_migration_is_named_instead_of_a_column_error(rf):
    middleware = _middleware(["tasks.0014_task_requires_review"])
    response = middleware(rf.get("/dashboard/"))

    assert response.status_code == 503
    body = response.content.decode()
    assert "tasks.0014_task_requires_review" in body
    assert "manage.py migrate" in body
    # The view never ran — this is the middleware's own page.
    assert response.content != b"view"


def test_the_check_stops_once_everything_is_applied(rf):
    calls = []
    middleware = _middleware([], calls=calls)

    assert middleware(rf.get("/dashboard/")).content == b"view"
    assert middleware(rf.get("/dashboard/")).content == b"view"
    # One query per process, not one per request.
    assert len(calls) == 1


def test_it_keeps_checking_while_something_is_outstanding(rf):
    """So the page clears the moment migrate finishes, with no restart."""
    calls = []
    middleware = _middleware(["tasks.0014_task_requires_review"], calls=calls)

    assert middleware(rf.get("/dashboard/")).status_code == 503
    assert middleware(rf.get("/dashboard/")).status_code == 503
    assert len(calls) == 2


def test_an_unreachable_database_is_left_to_speak_for_itself(rf):
    """A DB outage is a different error; don't dress it up as a migration."""
    middleware = _middleware(None)
    assert middleware(rf.get("/dashboard/")).content == b"view"
