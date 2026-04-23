"""Per-IP rate limiting for unauthenticated form submissions.

Mirrors the ``lockout`` module's ``count_recent_failures`` shape: we use the
existing ``AuditLog`` table as the counter, scoped by action and IP, over a
short rolling window. No new table, no new dependency.

Applied to signup and password-reset endpoints only — the login flow has its
own per-email lockout in ``apps.accounts.lockout``.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from apps.audit.models import AuditAction, AuditLog
from apps.audit.services import log_action


THROTTLE_WINDOW = timedelta(minutes=10)
THROTTLE_THRESHOLD = 5


def _client_ip(request) -> str:
    return request.META.get("REMOTE_ADDR", "") or ""


def is_throttled(request, action: AuditAction) -> bool:
    """True if this IP has exceeded the per-action threshold in the window.

    ``record_attempt`` is called BEFORE ``is_throttled`` in the view layer,
    so the check is ``>`` (strict) rather than ``>=`` — the Nth attempt is
    still allowed; the (N+1)th is the one that trips.
    """
    ip = _client_ip(request)
    if not ip:
        return False
    since = timezone.now() - THROTTLE_WINDOW
    count = AuditLog.objects.filter(
        action=action,
        timestamp__gte=since,
        metadata__ip=ip,
    ).count()
    return count > THROTTLE_THRESHOLD


def record_attempt(request, action: AuditAction) -> None:
    log_action(action, actor=None, metadata={"ip": _client_ip(request)})


__all__ = [
    "THROTTLE_WINDOW",
    "THROTTLE_THRESHOLD",
    "is_throttled",
    "record_attempt",
]
