"""Forms for the wiki app."""

from __future__ import annotations

from django import forms

from .models import WikiPage


#: Page settings only a project lead may change.
LEAD_ONLY_FIELDS = ("locked", "show_on_overview", "visibility")


class WikiPageForm(forms.ModelForm):
    """Create/edit form for a :class:`WikiPage`.

    ``summary`` is the editor's note on what changed — it lands on the
    revision, not on the page, so it is a plain form field. ``base_revision``
    is the revision the editor loaded; the view compares it against the
    page's current revision to catch two people editing at once.
    """

    summary = forms.CharField(
        required=False,
        max_length=255,
        label="What changed",
        help_text="Optional. Shown in the page history beside your name.",
        widget=forms.TextInput(
            attrs={
                "class": "input input-bordered w-full",
                "placeholder": "e.g. added the sampling protocol",
            }
        ),
    )
    base_revision = forms.IntegerField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = WikiPage
        fields = ["title", "content_markdown", "locked", "show_on_overview", "visibility"]
        labels = {
            "title": "Page title",
            "content_markdown": "Page content",
            "locked": "Lock this page (leads only can edit)",
            "show_on_overview": "Show on Project Overview",
            "visibility": "Who can see it",
        }
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "e.g. Sampling protocol",
                    "autofocus": "autofocus",
                }
            ),
            "content_markdown": forms.Textarea(
                attrs={
                    # EasyMDE is initialised on this id in the template.
                    "id": "id_content_markdown",
                    "class": "textarea textarea-bordered w-full font-mono",
                    "rows": 18,
                }
            ),
            "locked": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "show_on_overview": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "visibility": forms.RadioSelect(attrs={"class": "radio"}),
        }

    def __init__(self, *args, is_project_manager: bool = False, **kwargs):
        """Only a lead may lock a page, put it on the Project Overview or set
        who can see it, so only a lead sees those fields.

        Dropping the fields (rather than disabling them) is what keeps a
        contributor's edit from silently clearing a lead's setting: with no
        field there is no value to apply, and the view passes ``None`` to
        leave it as it stands.
        """
        super().__init__(*args, **kwargs)
        self.is_project_manager = is_project_manager
        if not is_project_manager:
            for name in LEAD_ONLY_FIELDS:
                self.fields.pop(name)
        else:
            # Left out of a submission, a page keeps who can see it (a new
            # page gets the default, all contributors).
            self.fields["visibility"].required = False
        self.fields["title"].required = True
        self.fields["content_markdown"].required = False

    def clean_title(self) -> str:
        title = (self.cleaned_data.get("title") or "").strip()
        if not title:
            raise forms.ValidationError("Give the page a title.")
        return title
