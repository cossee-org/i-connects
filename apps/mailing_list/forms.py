"""Forms for the mailing list app."""

from __future__ import annotations

from django import forms


class MailingListMessageForm(forms.Form):
    subject = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={"class": "input input-bordered w-full"}),
    )
    body = forms.CharField(
        widget=forms.Textarea(
            attrs={
                "class": "textarea textarea-bordered w-full",
                "rows": 10,
            }
        ),
    )
