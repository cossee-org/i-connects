"""JSON Schema validation helpers for the forms_builder app.

We run two layers of validation when a user submits a dynamic form:

1. Django ``Form.is_valid()`` via the class produced by ``build_form_class``.
   This handles per-field coercion (int, float, bool, email, ...) and
   required-ness for the subset of schema shapes we render.
2. ``jsonschema`` on the cleaned data. This is the authoritative check and
   catches any constraint the renderer ignores -- patterns, min/max, array
   constraints, nested objects, enums across non-string types, etc.
"""

from __future__ import annotations

from typing import Any

import jsonschema
from jsonschema import Draft202012Validator


def validate_schema(schema: dict[str, Any]) -> list[str]:
    """Return a list of error messages if ``schema`` is not a valid JSON Schema.

    An empty list means the schema itself is well-formed. We do not require
    a schema to declare ``type`` at the top level.
    """
    try:
        Draft202012Validator.check_schema(schema)
    except jsonschema.exceptions.SchemaError as exc:
        return [str(exc.message)]
    return []


def validate_data(schema: dict[str, Any], data: dict[str, Any]) -> list[str]:
    """Validate ``data`` against ``schema``; return list of human messages."""
    validator = Draft202012Validator(schema)
    errors: list[str] = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in err.path) or "<root>"
        errors.append(f"{path}: {err.message}")
    return errors
