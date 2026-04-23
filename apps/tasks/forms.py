"""Forms for tasks, milestones, kanban columns, and task logs."""

from __future__ import annotations

from django import forms

from apps.core.enums import AutoAssignNew, CompletedAs, TaskStatus
from apps.projects.models import ProjectMember
from apps.tasks.models import KanbanBoard, Milestone, Task, TaskLog


_daisy_input = "input input-bordered w-full"
_daisy_textarea = "textarea textarea-bordered w-full"
_daisy_select = "select select-bordered w-full"


class TaskForm(forms.ModelForm):
    """Create / update a Task.

    The ``project`` kwarg scopes the choices for ``container``,
    ``milestone``, and ``assigned_members`` to the active project. The
    ``container`` field is optional at the form level so inline creation
    from a column can pre-fill it without surfacing the field in the UI.
    """

    deadline = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": _daisy_input}
        ),
    )
    start_date = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": _daisy_input}
        ),
    )

    class Meta:
        model = Task
        fields = [
            "name",
            "description",
            "container",
            "milestone",
            "assigned_members",
            "start_date",
            "deadline",
            "contribution_type",
            "auto_assign_new",
            "anonymous",
            "github_url",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": _daisy_input}),
            "description": forms.Textarea(attrs={"class": _daisy_textarea, "rows": 4}),
            "container": forms.Select(attrs={"class": _daisy_select}),
            "milestone": forms.Select(attrs={"class": _daisy_select}),
            "assigned_members": forms.SelectMultiple(
                attrs={"class": "select select-bordered w-full h-32"}
            ),
            "contribution_type": forms.Select(attrs={"class": _daisy_select}),
            "auto_assign_new": forms.Select(attrs={"class": _daisy_select}),
            "anonymous": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "github_url": forms.URLInput(attrs={
                "class": "input input-bordered w-full",
                "placeholder": "https://github.com/org/repo/issues/123",
            }),
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        if project is not None:
            self.fields["container"].queryset = KanbanBoard.objects.filter(project=project)
            self.fields["milestone"].queryset = Milestone.objects.filter(project=project)
            self.fields["milestone"].required = False
            self.fields["assigned_members"].queryset = ProjectMember.objects.filter(
                project=project, deleted=False
            )


class MilestoneForm(forms.ModelForm):
    start_date = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": _daisy_input}
        ),
    )
    end_date = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": _daisy_input}
        ),
    )

    class Meta:
        model = Milestone
        fields = ["name", "description", "start_date", "end_date"]
        widgets = {
            "name": forms.TextInput(attrs={"class": _daisy_input}),
            "description": forms.Textarea(attrs={"class": _daisy_textarea, "rows": 3}),
        }

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if start and end and start > end:
            self.add_error("start_date", "Start date must be before or equal to end date.")
        return cleaned


class KanbanBoardForm(forms.ModelForm):
    class Meta:
        model = KanbanBoard
        fields = ["name"]
        widgets = {"name": forms.TextInput(attrs={"class": _daisy_input})}


class TaskAssignForm(forms.Form):
    """Re-assign a task's members."""

    assigned_members = forms.ModelMultipleChoiceField(
        queryset=ProjectMember.objects.none(),
        required=False,
        widget=forms.SelectMultiple(
            attrs={"class": "select select-bordered w-full h-32"}
        ),
    )

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        if project is not None:
            self.fields["assigned_members"].queryset = ProjectMember.objects.filter(
                project=project, deleted=False
            )


class TaskLogForm(forms.ModelForm):
    """Record a manual TaskLog entry."""

    class Meta:
        model = TaskLog
        fields = ["status", "completed_as", "metadata"]
        widgets = {
            "status": forms.Select(
                choices=TaskStatus.choices, attrs={"class": _daisy_select}
            ),
            "completed_as": forms.Select(
                choices=CompletedAs.choices, attrs={"class": _daisy_select}
            ),
            "metadata": forms.Textarea(attrs={"class": _daisy_textarea, "rows": 2}),
        }


class TaskMilestoneForm(forms.Form):
    """Assign a task to a milestone (or clear it)."""

    milestone = forms.ModelChoiceField(
        queryset=Milestone.objects.none(),
        required=False,
        empty_label="— No milestone —",
        widget=forms.Select(attrs={"class": _daisy_select}),
    )

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        if project is not None:
            self.fields["milestone"].queryset = Milestone.objects.filter(project=project)
