"""Production settings."""

import os

from .base import *  # noqa: F401,F403

DEBUG = False

# SECRET_KEY must come from the environment in production.
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()
]

# Whitenoise compressed + hashed static files. Django 5.1 removed the old
# STATICFILES_STORAGE setting (it is silently ignored now), so this has to
# go through STORAGES. `collectstatic` runs at image build time (Dockerfile);
# the manifest it writes is what makes hashed URLs and long cache headers
# work, and it is strict: a template referencing a static file that does not
# exist raises at render time instead of quietly 404ing.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Security hardening.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "True").lower() == "true"
# The Docker health check hits /healthz/ over plain HTTP from inside the
# container. Without this exemption it would get a 301 to https://, which
# `curl -f` counts as success -- i.e. the check would pass without ever
# reaching the view or the database.
SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]
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
