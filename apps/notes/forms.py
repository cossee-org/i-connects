"""Forms for the notes app."""

from __future__ import annotations

from django import forms

from apps.core.enums import NoteVisibility

from .models import Note


class NoteForm(forms.ModelForm):
    """Create/edit form for a :class:`Note`."""

    class Meta:
        model = Note
        fields = ["title", "content_markdown", "visibility", "pinned"]
        widgets = {
            "title": forms.TextInput(
                attrs={"class": "input input-bordered w-full"}
            ),
            "content_markdown": forms.Textarea(
                attrs={
                    # EasyMDE is initialised on this ID in the template.
                    "id": "id_content_markdown",
                    "class": "textarea textarea-bordered w-full",
                    "rows": 10,
                }
            ),
            "visibility": forms.Select(
                attrs={"class": "select select-bordered w-full"}
            ),
            "pinned": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def __init__(self, *args, is_project_manager: bool = False, **kwargs):
        """``is_project_manager`` restricts the visibility choices.

        Only PMs may set a note visible to all contributors. Non-PM users
        are limited to PRIVATE or PM_ONLY.
        """
        super().__init__(*args, **kwargs)
        if not is_project_manager:
            self.fields["visibility"].choices = [
                (NoteVisibility.PRIVATE, "Private"),
                (NoteVisibility.PM_ONLY, "Project managers only"),
            ]
