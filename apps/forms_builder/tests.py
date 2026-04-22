"""Smoke tests for the forms_builder app."""

from __future__ import annotations

import pytest

from apps.forms_builder import schema_to_form, validators
from apps.forms_builder.models import FormVersion
from apps.forms_builder.services import (
    clone_version,
    create_form_with_initial_version,
)


def test_build_form_class_from_simple_schema():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "title": "Name"}},
        "required": ["name"],
    }
    FormCls = schema_to_form.build_form_class(schema)
    form = FormCls()
    assert "name" in form.fields
    assert form.fields["name"].required is True


def test_validate_data_rejects_invalid():
    """``validate_data`` returns a non-empty error list for bad input.

    (Not a jsonschema.ValidationError -- the helper flattens errors into
    a list of human-readable messages.)
    """
    schema = {
        "type": "object",
        "properties": {"age": {"type": "integer"}},
    }
    errors = validators.validate_data(schema, {"age": "not-a-number"})
    assert errors, "expected validation errors for non-integer input"


@pytest.mark.django_db
def test_create_version_clones_schema(user):
    form = create_form_with_initial_version(
        user=user, name="My form", json_schema={"type": "object", "title": "My form"}
    )
    assert FormVersion.objects.filter(form=form).count() == 1
    source = form.versions.first()
    clone_version(source=source)
    assert FormVersion.objects.filter(form=form).count() == 2
