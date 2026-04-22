"""Development settings."""

from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Allow running without a SECRET_KEY in the environment for local dev.
SECRET_KEY = "dev-insecure-change-me"  # noqa: S105

INTERNAL_IPS = ["127.0.0.1", "localhost"]
