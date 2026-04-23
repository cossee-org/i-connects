"""Django forms used by the forms_builder UI.

These are the *admin* forms -- i.e. the forms a user fills in to author a
Form template. The dynamic "fill out this form" forms are built by
``schema_to_form.build_form_class``.
"""

from __future__ import annotations

import json
from typing import Any

from django import forms

from .models import Form, FormVersion
from .validators import validate_schema


class FormMetaForm(forms.ModelForm):
    """Edit the (minimal) metadata stored on ``Form`` itself.

    The data model keeps names on ``FormVersion``, so this form is really
    just an ``archived`` toggle plus a "display name" we route into the
    latest version's ``name`` field for convenience.
    """

    display_name = forms.CharField(
        max_length=255,
        required=False,
        help_text="Shown in lists. Saved to the most recent version's name.",
        widget=forms.TextInput(attrs={"class": "input input-bordered w-full"}),
    )

    class Meta:
        model = Form
        fields: list[str] = ["archived"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            latest = self.instance.versions.order_by("-version").first()
            if latest is not None:
                self.fields["display_name"].initial = latest.name


class FormVersionForm(forms.ModelForm):
    """Author/edit a FormVersion's schema payload.

    ``json_schema`` and ``ui_schema`` are textareas that the browser-side
    jsoneditor writes into as stringified JSON; we parse them here.
    """

    json_schema = forms.CharField(
        widget=forms.Textarea(
            attrs={
                "rows": 20,
                "class": "textarea textarea-bordered w-full font-mono text-sm",
            }
        ),
        required=False,
        help_text="JSON Schema describing the form's fields.",
    )
    ui_schema = forms.CharField(
        widget=forms.Textarea(
            attrs={
                "rows": 10,
                "class": "textarea textarea-bordered w-full font-mono text-sm",
            }
        ),
        required=False,
        help_text="Optional UI schema (rjsf-style).",
    )

    class Meta:
        model = FormVersion
        fields: list[str] = ["name"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Seed the textareas from the JSONField values on an existing row.
        if self.instance and self.instance.pk:
            self.fields["json_schema"].initial = json.dumps(
                self.instance.json_schema or {}, indent=2
            )
            if self.instance.ui_schema is not None:
                self.fields["ui_schema"].initial = json.dumps(
                    self.instance.ui_schema, indent=2
                )

    def _parse_json(self, field: str, *, allow_empty: bool) -> Any:
        raw = (self.cleaned_data.get(field) or "").strip()
        if not raw:
            if allow_empty:
                return None
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f"Invalid JSON: {exc.msg} (line {exc.lineno})")

    def clean_json_schema(self) -> dict[str, Any]:
        value = self._parse_json("json_schema", allow_empty=False) or {}
        if not isinstance(value, dict):
            raise forms.ValidationError("JSON schema must be an object at the top level.")
        errors = validate_schema(value)
        if errors:
            raise forms.ValidationError(errors)
        return value

    def clean_ui_schema(self) -> Any:
        value = self._parse_json("ui_schema", allow_empty=True)
        if value is not None and not isinstance(value, dict):
            raise forms.ValidationError("UI schema must be an object or empty.")
        return value


class AttachFormForm(forms.Form):
    """Pick one of the current user's form versions to attach to a Project/Task."""

    form_version = forms.ModelChoiceField(
        queryset=FormVersion.objects.none(),
        label="Form version",
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
    )

    def __init__(self, *args: Any, user=None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["form_version"].queryset = (
                FormVersion.objects.filter(
                    form__user=user,
                    form__archived=False,
                    archived=False,
                ).select_related("form").order_by("-form_id", "-version")
            )
