"""Base settings for i-connects Django MVP.

Environment-specific overrides live in ``dev.py`` and ``prod.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths & environment
# ---------------------------------------------------------------------------
# BASE_DIR = .../iconnects-django/
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load .env from project root if present (safe if missing).
load_dotenv(BASE_DIR / ".env")


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.orcid",
    "django_celery_beat",
    "crispy_forms",
    "crispy_bootstrap5",
    "django_countries",
]

LOCAL_APPS = [
    "apps.accounts",
    "apps.core",
    "apps.projects",
    "apps.tasks",
    "apps.roles",
    "apps.comments",
    "apps.notifications",
    "apps.notes",
    "apps.wiki",
    "apps.communications",
    "apps.forms_builder",
    "apps.contributions",
    "apps.audit",
    "apps.mailing_list",
    "apps.integrations",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Turns "column ... does not exist" into "run migrate". Removes itself
    # from the chain when DEBUG is off — see apps.core.middleware.
    "apps.core.middleware.UnappliedMigrationsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # allauth middleware is required in 0.56+
    "allauth.account.middleware.AccountMiddleware",
]


# ---------------------------------------------------------------------------
# URL / WSGI / ASGI
# ---------------------------------------------------------------------------
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.projects.context_processors.sidebar_counts",
                "apps.notifications.context_processors.unread_notifications",
                "apps.core.context_processors.module_visibility",
            ],
        },
    },
]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASES = {
    "default": dj_database_url.config(
        default=env(
            "DATABASE_URL",
            "postgres://iconnects:iconnects@db:5432/iconnects",
        ),
        conn_max_age=600,
    ),
}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

SITE_ID = 1

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Authentication — accounts app + allauth (Phase 3A)
# ---------------------------------------------------------------------------
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# Allauth: use email (not username) as the primary identifier, consistent
# with our custom User model.
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_REQUIRED = True

ACCOUNT_ADAPTER = "apps.accounts.adapters.AccountAdapter"
SOCIALACCOUNT_ADAPTER = "apps.accounts.adapters.SocialAccountAdapter"

# ORCID provider. Defaults to the sandbox; flip ORCID_SANDBOX=false in the
# environment to hit production orcid.org.
SOCIALACCOUNT_PROVIDERS = {
    "orcid": {
        "BASE_DOMAIN": (
            "sandbox.orcid.org"
            if (env("ORCID_SANDBOX", "true") or "true").lower() == "true"
            else "orcid.org"
        ),
    },
}


# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------
# Static & media
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------------
# Crispy forms
# ---------------------------------------------------------------------------
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"


# ---------------------------------------------------------------------------
# Celery / Redis
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", "redis://redis:6379/0")
CELERY_BROKER_URL = env("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", REDIS_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
# django-celery-beat's DatabaseScheduler, wrapped so it touches a heartbeat
# file after every successful tick. Celery beat has no ping endpoint, so this
# file's age is what the celery-beat container's Docker health check reads
# (see apps.core.celery_scheduler and docker-compose.yml).
CELERY_BEAT_SCHEDULER = "apps.core.celery_scheduler:HeartbeatDatabaseScheduler"
BEAT_HEARTBEAT_FILE = env("BEAT_HEARTBEAT_FILE", "/tmp/celerybeat-heartbeat")


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
# Dispatch Django's EMAIL_BACKEND by the ``EMAIL_BACKEND_CHOICE`` env var so
# the same settings file works for dev (console), self-hosted (smtp), and
# managed providers (resend, ses).
_EMAIL_BACKENDS = {
    "console": "django.core.mail.backends.console.EmailBackend",
    "smtp": "django.core.mail.backends.smtp.EmailBackend",
    "resend": "apps.core.email_backends.ResendEmailBackend",
    "ses": "django_ses.SESBackend",
}
EMAIL_BACKEND_CHOICE = env("EMAIL_BACKEND_CHOICE", "console")
EMAIL_BACKEND = _EMAIL_BACKENDS.get(
    EMAIL_BACKEND_CHOICE, _EMAIL_BACKENDS["console"]
)

# SMTP (used when EMAIL_BACKEND_CHOICE=smtp).
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = int(env("EMAIL_PORT", "587") or 587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)

# Resend (https://resend.com).
RESEND_API_KEY = env("RESEND_API_KEY", "")

# Amazon SES (via django-ses).
AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY", "")
AWS_SES_REGION_NAME = env("AWS_SES_REGION_NAME", "us-east-1")
AWS_SES_REGION_ENDPOINT = env("AWS_SES_REGION_ENDPOINT", "email.us-east-1.amazonaws.com")

DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "i-connects <noreply@iconnects.local>")
SITE_URL = env("SITE_URL", "")

# Zulip organisations a project may be connected to, by host suffix. Zulip
# Cloud by default; add a self-hosted server's host here (comma-separated).
# The allowlist is what stops a Lead pointing the server at an internal host.
ZULIP_ALLOWED_HOST_SUFFIXES = tuple(
    h.strip().lower()
    for h in (env("ZULIP_ALLOWED_HOSTS", "zulipchat.com") or "").split(",")
    if h.strip()
)
# The daily deadline reminder lists tasks due within this many days.
ZULIP_DEADLINE_WINDOW_DAYS = int(env("ZULIP_DEADLINE_WINDOW_DAYS", "3") or 3)

# Surveys and Communications are hidden for now: their sidebar links and
# project tabs are not drawn. The pages and routes are untouched, so turning
# this on brings both back as they were.
SHOW_SURVEYS_AND_COMMUNICATIONS = env_bool("SHOW_SURVEYS_AND_COMMUNICATIONS", False)

# The Roles column of the Contributors table is hidden for now. Members keep
# their roles; turning this on shows the column again.
SHOW_MEMBER_ROLES = env_bool("SHOW_MEMBER_ROLES", False)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# Django's own default LOGGING config only sends "django" (including
# django.request, which is what logs an unhandled 500) to the console
# when DEBUG is True; with DEBUG=False its sole handler is mail_admins,
# which is a silent no-op without an ADMINS setting. Left as the default,
# a production 500 leaves no trace anywhere docker/journald can see.
# Force a console handler unconditionally so tracebacks always reach
# `docker compose logs web`, whatever DEBUG is set to.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
