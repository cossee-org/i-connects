"""Forms behind a lead's corrections to the contributions ledger."""

from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model

from apps.core.enums import ContributionType, credit_role_choices
from apps.tasks.models import Task

from .models import ContributionRecord
from .services import project_member_users, user_display_name

_daisy_select = "select select-bordered w-full"


class ContributionEditForm(forms.Form):
    """Correct the role one contribution is recorded under.

    The role is all that is editable: who did what task is a fact about the
    project's work, and moving a record onto a different task or person
    would be indistinguishable from withdrawing it and adding another.

    Deliberately not a ModelForm — validating one writes the new value onto
    the instance, which would leave the service unable to tell what the
    record used to say.
    """

    contribution_type = forms.ChoiceField(
        label="CRediT role",
        choices=(),
        widget=forms.Select(attrs={"class": _daisy_select}),
    )

    def __init__(self, *args, record: ContributionRecord | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        current = record.contribution_type if record is not None else None
        self.fields["contribution_type"].choices = credit_role_choices(current)
        if record is not None and not self.is_bound:
            self.fields["contribution_type"].initial = current


class ContributionAddForm(forms.Form):
    """Credit someone on a task the automatic writer passed over.

    Scoped to the project throughout: only its tasks and only its members,
    so a correction cannot reach outside the project it was made from.
    """

    task = forms.ModelChoiceField(
        queryset=Task.objects.none(),
        empty_label="Select a task",
        widget=forms.Select(attrs={"class": _daisy_select}),
    )
    user = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(),
        label="Member",
        empty_label="Select a member",
        widget=forms.Select(attrs={"class": _daisy_select}),
    )
    contribution_type = forms.ChoiceField(
        label="CRediT role",
        choices=credit_role_choices(),
        widget=forms.Select(attrs={"class": _daisy_select}),
    )

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        if project is None:
            return
        self.fields["task"].queryset = Task.objects.filter(
            project=project
        ).order_by("name")
        self.fields["task"].label_from_instance = lambda task: task.name
        member_users = project_member_users(project)
        self.fields["user"].queryset = get_user_model().objects.filter(
            pk__in=[u.pk for u in member_users]
        ).order_by("given_name", "family_name", "name", "email")
        self.fields["user"].label_from_instance = user_display_name
        # "Other" is ours rather than CRediT's, but work that fits none of the
        # 14 roles needs somewhere to go, so it is offered last, as on a
        # task's type (TaskForm._init_task_type).
        choices = credit_role_choices()
        choices.append((ContributionType.OTHER, ContributionType.OTHER.label))
        self.fields["contribution_type"].choices = choices
