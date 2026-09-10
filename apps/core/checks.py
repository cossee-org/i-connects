"""Django system checks for deploy-time email misconfiguration.

Registered so ``manage.py migrate`` (which runs all checks by default,
and is the first command the production entrypoint runs) surfaces these
loudly before gunicorn even starts — instead of the only symptom being a
bounced email the first time a real one goes out.
"""

from __future__ import annotations

from email.utils import parseaddr

from django.conf import settings
from django.core.checks import Warning, register
from django.core.exceptions import ValidationError
from django.core.validators import validate_email


@register()
def check_default_from_email(app_configs, **kwargs):
    value = getattr(settings, "DEFAULT_FROM_EMAIL", "") or ""
    _, address = parseaddr(value)
    try:
        validate_email(address)
    except ValidationError:
        return [
            Warning(
                f"DEFAULT_FROM_EMAIL={value!r} is not a valid address once "
                "parsed. A display name must be wrapped around the address "
                'in angle brackets, e.g. "i-connects <you@gmail.com>" — '
                "without them, the whole string (including the space) is "
                "read as the address, which then mismatches the SMTP-"
                "authenticated account and gets outgoing mail rejected by "
                "the recipient (Gmail in particular, with a DKIM/alignment "
                "failure).",
                id="core.W001",
            )
        ]
    return []


@register()
def check_site_url(app_configs, **kwargs):
    if not settings.DEBUG and not getattr(settings, "SITE_URL", ""):
        return [
            Warning(
                "SITE_URL is not set. Emails sent from request-less contexts "
                "(invitations, join requests, digests) will link to a bare "
                "relative path instead of an absolute URL, which mail "
                "clients mangle into a broken link.",
                id="core.W002",
            )
        ]
    return []
