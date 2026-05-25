"""Test settings.

Overrides the dev settings so the suite runs without a live Postgres or
Redis service. Uses an in-memory SQLite DB and fast, non-networked email
and Celery backends.
"""

from .dev import *  # noqa: F401,F403

# In-memory SQLite keeps the suite fast and dependency-free.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Email stays in-memory so ``send_mail`` calls do nothing at the network.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Run Celery tasks inline; ``task.delay()`` becomes a direct call.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Fast, deterministic password hasher for tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Disable the per-IP signup / password-reset throttle by default in tests.
# Tests that exercise the throttle (``apps/accounts/throttle_tests.py``)
# re-enable it with ``@override_settings(THROTTLE_ENABLED=True)``.
THROTTLE_ENABLED = False
