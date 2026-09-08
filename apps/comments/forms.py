"""Comment form."""

from __future__ import annotations

from django import forms

from apps.comments.models import Comment


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ["content"]
        widgets = {
            # One row. DaisyUI's .textarea carries min-height: 3rem, so the
            # box lands at the same 48px as a single-line .input rather than
            # the 74px two rows of its 2.0 line-height produced. It is still
            # a textarea: newlines and the drag handle both work.
            "content": forms.Textarea(
                attrs={
                    "class": "textarea textarea-bordered w-full",
                    "rows": 1,
                    "placeholder": "Add a comment",
                }
            ),
        }
