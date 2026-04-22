"""Dynamic JSON-Schema -> Django Form renderer.

The original Blitz code uses ``@rjsf/core`` on the client. Since this Django
rewrite renders server-side, we instead translate a (small) subset of JSON
Schema into a Django ``forms.Form`` subclass at request time.

Supported field shapes::

    {"type": "string"}                          -> CharField
    {"type": "string", "format": "email"}       -> EmailField
    {"type": "string", "format": "textarea"}    -> CharField(widget=Textarea)
    {"type": "integer"}                         -> IntegerField
    {"type": "number"}                          -> FloatField
    {"type": "boolean"}                         -> BooleanField
    {"type": "string", "enum": [...]}           -> ChoiceField

Plus top-level ``required`` / ``title`` / ``description`` on each property.
Anything else (arrays, nested objects, oneOf/anyOf, $ref, patternProperties,
additionalProperties, dependent schemas) is deliberately **not** implemented
here -- see the module docstring in ``validators.py`` for how unsupported
shapes are still validated on submit.
"""

from __future__ import annotations

from typing import Any

from django import forms


TYPE_MAP: dict[str, type[forms.Field]] = {
    "string": forms.CharField,
    "integer": forms.IntegerField,
    "number": forms.FloatField,
    "boolean": forms.BooleanField,
}


def build_form_class(
    schema: dict[str, Any],
    *,
    class_name: str = "DynamicSchemaForm",
) -> type[forms.Form]:
    """Dynamically build a Django ``Form`` subclass from a JSON schema dict."""
    attrs: dict[str, forms.Field] = {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    for field_name, spec in properties.items():
        if not isinstance(spec, dict):
            continue
        attrs[field_name] = _field_from_spec(spec, field_name in required)
    return type(class_name, (forms.Form,), attrs)


def _field_from_spec(spec: dict[str, Any], required: bool) -> forms.Field:
    kind = spec.get("type", "string")
    kwargs: dict[str, Any] = {
        "required": required,
        "label": spec.get("title"),
        "help_text": spec.get("description") or "",
    }

    # Enum -> ChoiceField regardless of declared type.
    if isinstance(spec.get("enum"), list) and spec["enum"]:
        choices = [(v, str(v)) for v in spec["enum"]]
        if not required:
            choices = [("", "---------"), *choices]
        return forms.ChoiceField(choices=choices, **kwargs)

    if kind == "string":
        fmt = spec.get("format")
        if fmt == "email":
            return forms.EmailField(**kwargs)
        if fmt == "textarea":
            kwargs["widget"] = forms.Textarea(attrs={"rows": 4})
            return forms.CharField(**kwargs)
        if fmt == "date":
            return forms.DateField(**kwargs)
        return forms.CharField(**kwargs)

    field_cls = TYPE_MAP.get(kind, forms.CharField)
    # BooleanField required=True in Django means "must be True" -- for JSON
    # schema boolean fields we almost always want a tri-state toggle, so use
    # NullBooleanField when not required.
    if kind == "boolean" and not required:
        return forms.NullBooleanField(
            label=kwargs["label"],
            help_text=kwargs["help_text"],
            required=False,
        )
    return field_cls(**kwargs)
