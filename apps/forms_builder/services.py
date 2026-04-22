"""Small procedural helpers for the forms_builder app.

Kept deliberately thin -- mirrors the style of ``apps/projects/services.py``.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from .models import Form, FormVersion


def _derive_name(schema: dict[str, Any], fallback: str) -> str:
    title = schema.get("title") if isinstance(schema, dict) else None
    if isinstance(title, str) and title.strip():
        return title.strip()[:255]
    return fallback


@transaction.atomic
def create_form_with_initial_version(
    *,
    user,
    name: str,
    json_schema: dict[str, Any] | None = None,
    ui_schema: dict[str, Any] | None = None,
) -> Form:
    """Create a ``Form`` plus its first ``FormVersion`` (version=1)."""
    form = Form.objects.create(user=user)
    FormVersion.objects.create(
        form=form,
        version=1,
        name=_derive_name(json_schema or {}, name or "Untitled form"),
        json_schema=json_schema or {},
        ui_schema=ui_schema,
    )
    return form


@transaction.atomic
def create_version(
    *,
    form: Form,
    json_schema: dict[str, Any],
    ui_schema: dict[str, Any] | None = None,
    name: str | None = None,
) -> FormVersion:
    """Append a new (immutable) version row to an existing form."""
    last = form.versions.order_by("-version").first()
    next_version = (last.version + 1) if last else 1
    return FormVersion.objects.create(
        form=form,
        version=next_version,
        name=_derive_name(json_schema, name or (last.name if last else "Untitled form")),
        json_schema=json_schema,
        ui_schema=ui_schema,
    )


@transaction.atomic
def clone_version(*, source: FormVersion) -> FormVersion:
    """Duplicate ``source`` as the next version on the same form."""
    return create_version(
        form=source.form,
        json_schema=source.json_schema or {},
        ui_schema=source.ui_schema,
        name=source.name,
    )


def archive_form(*, form: Form) -> None:
    """Soft-delete a form (owner-level archive)."""
    if not form.archived:
        form.archived = True
        form.save(update_fields=["archived", "updated_at"])


def archive_version(*, version: FormVersion) -> None:
    """Soft-delete a single form version."""
    if not version.archived:
        version.archived = True
        version.save(update_fields=["archived"])
