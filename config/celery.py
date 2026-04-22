"""Celery application for i-connects.

Autodiscovers ``tasks`` submodules in every installed Django app, including
``apps.*``. The ``config.__init__`` module imports ``celery_app`` so Django
picks the app up at startup.
"""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("iconnects")

# Pull any CELERY_* settings from Django settings.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover ``tasks.py`` in every INSTALLED_APPS entry (includes ``apps.*``)
# and in the top-level ``cron`` package.
app.autodiscover_tasks()
app.autodiscover_tasks(["cron"])


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
