"""Public API for the audit log.

Callers should use ``log_action`` and pass whatever context they have — the
service normalises ``entity`` objects into their ``entity_type`` /
``entity_id`` representation and accepts raw fields for unusual cases.

The service is intentionally synchronous and atomic-friendly: it writes one
row with no side effects. If logging ever becomes a hot path, the call can
be moved to a post-commit hook or a background job without changing the
public signature.
"""

from __future__ import annotations

from typing import Any, Optional

from django.db import models as dj_models

from .models import AuditAction, AuditLog


def log_action(
    action: str | AuditAction,
    *,
    actor=None,
    entity: Optional[dj_models.Model] = None,
    entity_type: str = "",
    entity_id: Optional[int] = None,
    project=None,
    metadata: Optional[dict[str, Any]] = None,
) -> AuditLog:
    """Append a single AuditLog row.

    Parameters
    ----------
    action
        The ``AuditAction`` value (or its string form, e.g. ``"project.created"``).
    actor
        The User performing the action. May be ``None`` for system-initiated
        events (anonymous login failures, scheduled jobs).
    entity
        Optional Django model instance whose ``(_meta.model_name, pk)`` is
        stored on the log for later lookup. Mutually exclusive with
        ``entity_type`` / ``entity_id``; if both are given, ``entity`` wins.
    project
        Optional Project for project-scoped filtering. Many actions imply a
        project (task, invitation, member change) — pass it when you have it.
    metadata
        Arbitrary JSON-serialisable payload. Keep it small and stable.
    """

    if entity is not None:
        entity_type = entity._meta.model_name
        entity_id = entity.pk

    # A User may have been deleted (or we may be logging a signup with a
    # Django User that is not yet saved); normalise to a real PK.
    if actor is not None and getattr(actor, "pk", None) is None:
        actor = None

    return AuditLog.objects.create(
        action=str(action),
        actor=actor,
        entity_type=entity_type or "",
        entity_id=entity_id,
        project=project,
        metadata=metadata or None,
    )


__all__ = ["AuditAction", "log_action"]
