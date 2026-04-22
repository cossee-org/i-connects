"""Forms for the projects app."""

from __future__ import annotations

from django import forms

from apps.core.enums import MemberPrivileges

from .models import Invitation, Project


class ProjectForm(forms.ModelForm):
    """Create / edit a project."""

    class Meta:
        model = Project
        fields = [
            "name",
            "description",
            "abstract",
            "keywords",
            "status",
            "is_public",
            "public_summary",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "description": forms.Textarea(
                attrs={"class": "textarea textarea-bordered w-full", "rows": 4}
            ),
            "abstract": forms.Textarea(
                attrs={"class": "textarea textarea-bordered w-full", "rows": 3}
            ),
            "keywords": forms.TextInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "comma, separated, tags",
                }
            ),
            "status": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "is_public": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "public_summary": forms.Textarea(
                attrs={"class": "textarea textarea-bordered w-full", "rows": 3}
            ),
        }


class InvitationForm(forms.ModelForm):
    """Invite someone by email to a project."""

    roles = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Invitation
        fields = ["email", "privilege", "roles"]
        widgets = {
            "email": forms.EmailInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "collaborator@example.org",
                }
            ),
            "privilege": forms.Select(attrs={"class": "select select-bordered w-full"}),
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Avoid a top-level import cycle with the roles app.
        from apps.roles.models import Role

        self.project = project
        if project is not None:
            self.fields["roles"].queryset = Role.objects.filter(project=project)
        else:
            self.fields["roles"].queryset = Role.objects.none()


class MemberPrivilegeForm(forms.Form):
    """Change a ProjectMember's privilege (toggle PM <-> CONTRIBUTOR)."""

    privilege = forms.ChoiceField(
        choices=MemberPrivileges.choices,
        widget=forms.Select(attrs={"class": "select select-bordered select-sm"}),
    )
