"""Forms for tasks, milestones, kanban columns, and task logs."""

from __future__ import annotations

import json

from django import forms
from django.db.models import Min
from django.utils.html import format_html

from apps.core.enums import CompletedAs, ContributionType, TaskStatus
from apps.projects.models import ProjectMember
from apps.tasks.models import KanbanBoard, Milestone, Task, TaskLog


_daisy_input = "input input-bordered w-full"
_daisy_textarea = "textarea textarea-bordered w-full"
_daisy_select = "select select-bordered w-full"


def member_label(member: ProjectMember) -> str:
    """Render a project member as ``"Given Name Last Name (email)"``.

    Falls back to the member's name (or the user's ``name``/email) when the
    structured given/family names are not populated.
    """

    user = member.users.order_by("given_name", "family_name").first()
    if user is None:
        return member.name or f"Member #{member.pk}"
    first = (user.given_name or "").strip()
    last = (user.family_name or "").strip()
    display = f"{first} {last}".strip()
    if not display:
        display = (user.name or user.email).strip()
    return f"{display} ({user.email})"


class MemberMultiSelect(forms.SelectMultiple):
    """A DaisyUI/Alpine.js multi-select dropdown of project members.

    Renders a collapsible dropdown of checkboxes (one per member) plus an
    "All" option that selects every member — leads included, since leads
    are project members carrying the project-manager privilege. Each
    checkbox submits under the field name, so server-side parsing stays
    identical to a native ``<select multiple>``.
    """

    def render(self, name, value, attrs=None, renderer=None):
        selected = {str(v) for v in (value or [])}
        all_ids = []
        members_data = []
        for option_value, option_label in self.choices:
            val = str(option_value)
            all_ids.append(val)
            members_data.append({"id": val, "label": str(option_label)})

        x_data = json.dumps({
            "ids": sorted(selected),
            "allIds": all_ids,
            "search": "",
            "members": members_data,
        })

        # Built on the native <details>/<summary> disclosure widget so the
        # toggle works in every browser (notably Safari, where click handlers
        # on <div>/<button> proxies are unreliable) — and even if Alpine fails
        # to load. Alpine only manages the checkbox state + summary label; the
        # checkboxes are real form inputs, so submission works regardless.
        # The dropdown opens upward (bottom-full) for better readability in modals.
        return format_html(
            '<details x-data="{}" class="dropdown w-full">'
            '<summary class="select select-bordered w-full flex items-center justify-between '
            'cursor-pointer text-left font-normal">'
            '<span x-text="ids.length ? (ids.length === allIds.length ? '
            "'All members' : ids.length + ' selected') : 'Select members'\">Select members</span>"
            '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 opacity-60 pointer-events-none" '
            'fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">'
            '<path stroke-linecap="round" stroke-linejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5"/></svg>'
            "</summary>"
            '<div class="dropdown-content absolute left-0 bottom-full z-30 bg-base-100 rounded-box shadow '
            'border border-base-300 w-full mb-1 p-2 max-h-64 overflow-y-auto">'
            '<input type="text" x-model="search" placeholder="Search members…" '
            '@click.stop '
            'class="input input-sm input-bordered w-full mb-2">'
            '<label class="label cursor-pointer justify-start gap-2 py-1" x-show="!search">'
            '<input type="checkbox" class="checkbox checkbox-sm" '
            ':checked="allIds.length && ids.length === allIds.length" '
            '@change="ids = $event.target.checked ? [...allIds] : []">'
            '<span class="label-text font-semibold">All</span>'
            "</label>"
            '<div class="divider my-0" x-show="!search"></div>'
            '<template x-for="member in members.filter('
            "m => !search || m.label.toLowerCase().includes(search.toLowerCase())"
            ')" :key="member.id">'
            '<label class="label cursor-pointer justify-start gap-2 py-1">'
            '<input type="checkbox" name="{}" :value="member.id" x-model="ids" '
            'class="checkbox checkbox-sm">'
            '<span class="label-text" x-text="member.label"></span>'
            "</label>"
            "</template>"
            "</div>"
            "</details>",
            x_data,
            name,
        )


class TaskForm(forms.ModelForm):
    """Create / update a Task.

    The ``project`` kwarg scopes the choices for ``container``,
    ``assigned_members``, and ``reviewers`` to the active project. The
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
            "assigned_members",
            "reviewers",
            "start_date",
            "deadline",
            "contribution_type",
            "completion_mode",
        ]
        widgets = {
            "name": forms.TextInput(attrs={
                "class": _daisy_input,
                "maxlength": "255",
                "@input": "nameCharCount = $event.target.value.length",
            }),
            "description": forms.Textarea(attrs={
                "class": _daisy_textarea,
                "rows": 4,
                "maxlength": "5000",
                "@input": "descCharCount = $event.target.value.length",
            }),
            "container": forms.Select(attrs={"class": _daisy_select}),
            "assigned_members": MemberMultiSelect(),
            "reviewers": MemberMultiSelect(),
            "contribution_type": forms.Select(attrs={"class": _daisy_select}),
            "completion_mode": forms.Select(attrs={"class": _daisy_select}),
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        self.fields["name"].label = "Task name"
        self.fields["assigned_members"].label = "Assignees"
        self.fields["reviewers"].label = "Reviewers"
        self.fields["completion_mode"].label = "Completion mode"
        self.fields["deadline"].label = "Due date"
        self.fields["container"].label = "Status"
        self.fields["container"].required = True
        # Prompt text shown for the unselected (blank) choice.
        self.fields["container"].empty_label = "Select status"
        # Show only the container's name in the dropdown (e.g. "In Progress"),
        # not the model's default "{name} (project {id})" representation.
        self.fields["container"].label_from_instance = lambda obj: obj.name
        # Sort contribution types by name, keeping "Other" last (and any
        # leading blank choice at the top).
        ct_field = self.fields["contribution_type"]
        choices = list(ct_field.choices)
        blanks = [c for c in choices if c[0] in ("", None)]
        other = [c for c in choices if c[0] == ContributionType.OTHER]
        rest = sorted(
            (c for c in choices if c[0] not in ("", None) and c[0] != ContributionType.OTHER),
            key=lambda c: c[1],
        )
        ct_field.choices = blanks + rest + other
        ct_field.required = False
        if project is not None:
            self.fields["container"].queryset = KanbanBoard.objects.filter(project=project)
            members = (
                ProjectMember.objects.filter(project=project, deleted=False)
                .annotate(sort_name=Min("users__given_name"))
                .order_by("sort_name")
            )
            # Render members as "Given Name Last Name (email)", sorted by given name.
            for field_name in ("assigned_members", "reviewers"):
                self.fields[field_name].queryset = members
                self.fields[field_name].label_from_instance = member_label


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
            members = (
                ProjectMember.objects.filter(project=project, deleted=False)
                .annotate(sort_name=Min("users__given_name"))
                .order_by("sort_name")
            )
            # Render members as "First Name Last Name (email)", sorted by given name.
            self.fields["assigned_members"].queryset = members
            self.fields["assigned_members"].label_from_instance = member_label


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
