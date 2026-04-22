"""Forms for the roles app."""

from __future__ import annotations

from django import forms

from .models import Role


class RoleForm(forms.ModelForm):
    class Meta:
        model = Role
        fields = ["name", "description", "taxonomy"]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": "input input-bordered input-sm w-full"}
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "textarea textarea-bordered textarea-sm w-full",
                    "rows": 2,
                }
            ),
            "taxonomy": forms.TextInput(
                attrs={"class": "input input-bordered input-sm w-full"}
            ),
        }
