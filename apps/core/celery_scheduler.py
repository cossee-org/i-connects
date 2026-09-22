"""Celery beat scheduler that leaves a liveness trace for the container health check.

Celery beat has no ``ping``: ``celery inspect ping`` reaches workers only, and
a process-alive check cannot tell a scheduler that is ticking from one that
is wedged on a database call. What beat *does* do, reliably, is call
``Scheduler.tick()`` in its main loop -- with django-celery-beat's
``DatabaseScheduler`` at least every ``DEFAULT_MAX_INTERVAL`` (5 s), whether
or not anything is due.

So: after every tick that completes, touch a file. The ``celery-beat``
service's Docker health check (``docker-compose.yml``) then asks whether that
file was modified in the last minute. A tick that raises does not record a
heartbeat, and neither does a scheduler that never gets to tick at all.
"""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django_celery_beat.schedulers import DatabaseScheduler

logger = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_FILE = "/tmp/celerybeat-heartbeat"


def heartbeat_file() -> Path:
    """Where the heartbeat is written; ``BEAT_HEARTBEAT_FILE`` in settings."""
    return Path(getattr(settings, "BEAT_HEARTBEAT_FILE", None) or DEFAULT_HEARTBEAT_FILE)


class HeartbeatDatabaseScheduler(DatabaseScheduler):
    """``DatabaseScheduler`` that touches :func:`heartbeat_file` after each tick."""

    def tick(self, *args, **kwargs):
        interval = super().tick(*args, **kwargs)
        self._record_heartbeat()
        return interval

    @staticmethod
    def _record_heartbeat() -> None:
        path = heartbeat_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        except OSError as exc:
            # Never let bookkeeping take the scheduler down; the health
            # check will notice the stale file and say so.
            logger.warning("beat heartbeat: could not touch %s: %s", path, exc)
