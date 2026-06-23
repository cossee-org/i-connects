"""Comment form."""

from __future__ import annotations

from django import forms

from apps.comments.models import Comment


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ["content"]
        widgets = {
            "content": forms.Textarea(
                attrs={
                    "class": "textarea textarea-bordered w-full",
                    "rows": 2,
                    "placeholder": "Add a comment",
                }
            ),
        }
