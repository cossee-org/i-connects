"""Forms for the notifications app."""

from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from apps.projects.models import Project


class AnnouncementForm(forms.Form):
    """Admin-only form to broadcast a notification.

    If ``project`` is empty, the announcement is sent to every active user.
    Otherwise, it is sent to every user who is a member of that project.
    """

    title = forms.CharField(
        max_length=255,
        required=True,
        widget=forms.TextInput(attrs={"class": "input input-bordered w-full"}),
    )
    body = forms.CharField(
        required=True,
        widget=forms.Textarea(
            attrs={"class": "textarea textarea-bordered w-full", "rows": 4}
        ),
    )
    project = forms.ModelChoiceField(
        queryset=Project.objects.all(),
        required=False,
        empty_label="(All users system-wide)",
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
        help_text="Leave blank to broadcast to every user.",
    )
    link = forms.CharField(
        max_length=500,
        required=False,
        widget=forms.TextInput(attrs={"class": "input input-bordered w-full"}),
        help_text=(
            "Optional URL the notification should link to — either a path on "
            "this site (/projects/12/) or a full http(s) address."
        ),
    )

    def clean_link(self) -> str:
        """Reject anything that is not a path here or an http(s) address.

        The value is rendered as an ``href`` for every recipient, so a
        ``javascript:`` or ``data:`` URL would be script the announcer got
        to run in the reader's session.
        """
        link = (self.cleaned_data.get("link") or "").strip()
        if not link:
            return ""
        if link.startswith("//"):
            raise ValidationError(
                "Enter a path starting with / or a full http(s) address."
            )
        if link.startswith("/"):
            return link
        if link.lower().startswith(("http://", "https://")):
            return link
        raise ValidationError(
            "Enter a path starting with / or a full http(s) address."
        )
