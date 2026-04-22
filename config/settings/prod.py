"""Production settings."""

import os

from .base import *  # noqa: F401,F403

DEBUG = False

# SECRET_KEY must come from the environment in production.
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()
]

# Whitenoise compressed + hashed static files.
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# Security hardening.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "True").lower() == "true"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
)
