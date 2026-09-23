"""Smoke tests for shared cron tasks + management commands."""

from __future__ import annotations

import importlib
import re
import sys
from unittest import mock

import pytest
from django.core.exceptions import MiddlewareNotUsed
from django.core.management import call_command
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.utils import OperationalError
from django.http import HttpResponse
from django.urls import reverse
from django_celery_beat.models import PeriodicTask
from django_celery_beat.schedulers import DatabaseScheduler

from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog
from apps.core.celery_scheduler import HeartbeatDatabaseScheduler
from apps.core.enums import UserRole
from cron.tasks import daily_digest_cron


@pytest.mark.django_db
def test_admin_console_renders_with_actorless_audit_events(client):
    """A signup/lockout event with no actor (``actor`` is nullable via
    ``SET_NULL``) used to crash the whole page: ``{{ event.actor.name|
    default:event.actor.email }}`` resolves ``event.actor.email`` as a
    filter *argument*, and unlike a template's primary variable, a failed
    lookup there isn't swallowed into an empty string -- it raises
    ``VariableDoesNotExist`` straight through the render.
    """
    admin = User.objects.create_user(
        email="admin@example.com", password="x", role=UserRole.ADMIN
    )
    AuditLog.objects.create(actor=None, action=AuditAction.USER_SIGNUP)
    AuditLog.objects.create(actor=None, action=AuditAction.ACCOUNT_LOCKED_OUT)

    client.force_login(admin)
    resp = client.get(reverse("core:admin_console"))

    assert resp.status_code == 200


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


#: Utilities whose absence silently changes a layout rather than erroring:
#: a width, a height, or a grid's column count.
_LAYOUT_CLASS = re.compile(
    r"(?<![\w:-])((?:(?:sm|md|lg|xl|2xl):)*"
    r"(?:[wh]-\d+(?:\.\d+)?|grid-cols-\d+))(?![\w.-])"
)


def test_every_layout_class_a_template_uses_exists_in_the_built_css():
    """A utility no build has generated silently changes the layout.

    Tailwind only emits the utilities it finds in the content it scans, so a
    template that reaches for a size or a column count no other template
    uses depends on `npm run build:css` having been run since. Where it
    hasn't — a cached stylesheet, a checkout that skipped the step — the
    rule is simply missing: an element gets no width or height and shrinks
    to fit its content, so a `rounded-full` avatar stops being a circle; a
    grid falls back to one column. Nothing errors, so only the page shows
    it.
    """
    from pathlib import Path

    import config

    root = Path(config.__file__).resolve().parent.parent
    css = (root / "static" / "css" / "output.css").read_text(encoding="utf-8")

    offenders = []
    for path in sorted((root / "templates").rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            for name in _LAYOUT_CLASS.findall(line):
                # Tailwind escapes the responsive prefix's colon, and the
                # dot in fractional names like ``w-3.5``.
                escaped = name.replace(":", "\\:").replace(".", "\\.")
                if f".{escaped}{{" not in css:
                    rel = path.relative_to(root / "templates")
                    offenders.append(f"{rel}:{number}: {name}")

    assert not offenders, (
        "these layout classes are used in templates but absent from "
        "static/css/output.css — run `npm run build:css` and commit the "
        "result, or use a value the app already uses:\n" + "\n".join(offenders)
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


# ---------------------------------------------------------------------------
# /healthz/ -- what the web container's Docker health check calls
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_healthz_answers_ok_when_the_database_answers(client):
    response = client.get(reverse("core:healthz"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
    # A probe result must never be served from a cache.
    assert "no-store" in response["Cache-Control"]


@pytest.mark.django_db
def test_healthz_answers_503_when_the_database_query_fails(client):
    """A process that is up but cannot reach Postgres is *not* healthy."""
    with mock.patch.object(
        connections[DEFAULT_DB_ALIAS], "cursor", side_effect=OperationalError("gone")
    ):
        response = client.get(reverse("core:healthz"))

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "database": "error"}


@pytest.mark.django_db
def test_healthz_allows_head_and_rejects_writes(client):
    """Uptime monitors often probe with HEAD; nothing should ever POST here."""
    assert client.head(reverse("core:healthz")).status_code == 200
    assert client.post(reverse("core:healthz")).status_code == 405


def test_production_settings_wire_whitenoise_through_storages_and_exempt_healthz(
    monkeypatch,
):
    """Django 5.1 removed STATICFILES_STORAGE; only STORAGES is read now.

    Guards against the setting quietly coming back under its old name, and
    against the health check's redirect exemption being dropped (without
    it the in-container probe gets a 301, which ``curl -f`` treats as a
    pass without ever reaching the database).
    """
    monkeypatch.setenv("DJANGO_SECRET_KEY", "test-only")
    sys.modules.pop("config.settings.prod", None)
    prod = importlib.import_module("config.settings.prod")
    try:
        assert not hasattr(prod, "STATICFILES_STORAGE")
        assert (
            prod.STORAGES["staticfiles"]["BACKEND"]
            == "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
        assert any(re.search(pattern, "healthz/") for pattern in prod.SECURE_REDIRECT_EXEMPT)
    finally:
        sys.modules.pop("config.settings.prod", None)


# ---------------------------------------------------------------------------
# celery-beat heartbeat -- what the celery-beat container's health check reads
# ---------------------------------------------------------------------------


def _scheduler_without_init() -> HeartbeatDatabaseScheduler:
    # DatabaseScheduler.__init__ wants a Celery app and a live schedule
    # table; the heartbeat wrapper touches neither, so skip it.
    return HeartbeatDatabaseScheduler.__new__(HeartbeatDatabaseScheduler)


def test_beat_scheduler_records_a_heartbeat_after_each_successful_tick(
    tmp_path, settings, monkeypatch
):
    heartbeat = tmp_path / "beat" / "heartbeat"
    settings.BEAT_HEARTBEAT_FILE = str(heartbeat)
    monkeypatch.setattr(DatabaseScheduler, "tick", lambda self, *args, **kwargs: 5.0)

    assert _scheduler_without_init().tick() == 5.0

    assert heartbeat.exists()


def test_beat_scheduler_leaves_no_heartbeat_when_the_tick_fails(
    tmp_path, settings, monkeypatch
):
    """A tick that blows up (database gone) must read as *not* alive."""
    heartbeat = tmp_path / "heartbeat"
    settings.BEAT_HEARTBEAT_FILE = str(heartbeat)

    def broken_tick(self, *args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(DatabaseScheduler, "tick", broken_tick)

    with pytest.raises(RuntimeError):
        _scheduler_without_init().tick()

    assert not heartbeat.exists()
