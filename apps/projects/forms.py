"""Forms for the projects app."""

from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model

from apps.core.enums import MemberPrivileges

from .models import Invitation, JoinRequest, Project, ProjectPrivilege


class ProjectForm(forms.ModelForm):
    """Create / edit a project."""

    class Meta:
        model = Project
        fields = [
            "name",
            "description",
            "keywords",
            "status",
            "is_public",
        ]
        labels = {
            "name": "Title",
            "is_public": "Is public?",
        }
        widgets = {
            "name": forms.TextInput(attrs={"class": "input input-sm input-bordered w-full"}),
            "description": forms.Textarea(
                attrs={"class": "textarea textarea-sm textarea-bordered w-full", "rows": 4}
            ),
            "keywords": forms.TextInput(
                attrs={
                    "class": "input input-sm input-bordered w-full",
                    "placeholder": "comma, separated, tags",
                }
            ),
            "status": forms.Select(attrs={"class": "select select-sm select-bordered w-full"}),
            "is_public": forms.CheckboxInput(attrs={"class": "toggle toggle-sm"}),
        }


class InvitationForm(forms.ModelForm):
    """Invite someone to a project — either by picking an existing
    platform user or by typing an email address."""

    invited_user = forms.ModelChoiceField(
        queryset=None,
        required=False,
        widget=forms.Select(
            attrs={"class": "select select-bordered w-full"}
        ),
        help_text="Pick someone who already has an account, or leave blank and use the email field below.",
    )

    roles = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Invitation
        fields = ["invited_user", "email", "privilege", "roles"]
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
            User = get_user_model()
            existing_member_ids = ProjectPrivilege.objects.filter(
                project=project
            ).values_list("user_id", flat=True)
            self.fields["invited_user"].queryset = (
                User.objects.exclude(pk__in=existing_member_ids)
                .order_by("name", "email")
            )
        else:
            self.fields["roles"].queryset = Role.objects.none()
            self.fields["invited_user"].queryset = get_user_model().objects.none()

        # Email is required *unless* an existing user was picked.
        self.fields["email"].required = False

    def clean(self):
        cleaned = super().clean()
        invited_user = cleaned.get("invited_user")
        email = (cleaned.get("email") or "").strip()

        if not invited_user and not email:
            raise forms.ValidationError(
                "Pick an existing user or enter an email address."
            )

        if invited_user is not None:
            # Email is sourced from the user account when invited_user is set.
            cleaned["email"] = (invited_user.email or "").lower()
            if self.project is not None and ProjectPrivilege.objects.filter(
                project=self.project, user=invited_user
            ).exists():
                raise forms.ValidationError(
                    "That user is already a member of this project."
                )
        else:
            cleaned["email"] = email.lower()
        return cleaned


class JoinRequestForm(forms.ModelForm):
    """A user's request to join a project."""

    class Meta:
        model = JoinRequest
        fields = ["requested_privilege", "message"]
        widgets = {
            "requested_privilege": forms.Select(
                attrs={"class": "select select-bordered w-full"}
            ),
            "message": forms.Textarea(
                attrs={
                    "class": "textarea textarea-bordered w-full",
                    "rows": 4,
                    "placeholder": "Tell the project team why you'd like to join (optional).",
                }
            ),
        }

    def __init__(self, *args, initial_privilege=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["message"].required = False
        if initial_privilege and not self.is_bound:
            self.fields["requested_privilege"].initial = initial_privilege


class JoinRequestDeclineForm(forms.Form):
    """Optional decline-with-reason form for PMs."""

    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "textarea textarea-bordered w-full",
                "rows": 2,
                "placeholder": "Optional note for the requester.",
            }
        ),
    )


class MemberPrivilegeForm(forms.Form):
    """Change a ProjectMember's privilege (toggle PM <-> CONTRIBUTOR)."""

    privilege = forms.ChoiceField(
        choices=MemberPrivileges.choices,
        widget=forms.Select(attrs={"class": "select select-bordered select-sm"}),
    )
