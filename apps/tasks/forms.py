"""Forms for tasks, milestones, kanban columns, and task logs."""

from __future__ import annotations

import json

from django import forms
from django.db.models import Min
from django.utils.html import format_html

from apps.core.enums import (
    CompletedAs,
    ContributionType,
    TaskStatus,
    credit_role_choices,
)
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

    # datetime-local inputs require the HTML5 "YYYY-MM-DDTHH:MM" format for
    # both the value they submit and the value they're pre-filled with;
    # Django's default DATETIME_INPUT_FORMATS is space-separated, which the
    # browser silently rejects (the field just renders blank).
    deadline = forms.DateTimeField(
        required=False,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"],
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": _daisy_input},
            format="%Y-%m-%dT%H:%M",
        ),
    )
    start_date = forms.DateTimeField(
        required=False,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"],
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": _daisy_input},
            format="%Y-%m-%dT%H:%M",
        ),
    )
    # Asked only when editing a task some assignees have their own start
    # date / deadline on, and only acted on when the task's matching date
    # changes: what happens to those members' own dates. See
    # ``_init_individual_dates``.
    individual_start_dates = forms.ChoiceField(
        required=False,
        choices=[
            ("shift", "Shift them by the same amount"),
            ("keep", "Keep their dates as they are"),
            ("clear", "Remove them, so they follow the task's start date"),
        ],
        widget=forms.RadioSelect,
    )
    individual_deadlines = forms.ChoiceField(
        required=False,
        choices=[
            ("shift", "Shift them by the same amount"),
            ("keep", "Keep their dates as they are"),
            ("clear", "Remove them, so they follow the task's due date"),
        ],
        widget=forms.RadioSelect,
    )

    # Task date field -> the choice field asking about members' own dates.
    INDIVIDUAL_DATE_CHOICES = {
        "start_date": "individual_start_dates",
        "deadline": "individual_deadlines",
    }

    class Meta:
        model = Task
        fields = [
            "name",
            "description",
            "container",
            "assigned_members",
            "requires_review",
            "reviewers",
            "start_date",
            "deadline",
            "contribution_type",
            "is_priority",
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
            # Drawn as a switch in the Review panel (task_form.html).
            "requires_review": forms.CheckboxInput(
                attrs={"class": "toggle toggle-sm toggle-primary mt-0.5", "x-model": "requiresReview"}
            ),
            "contribution_type": forms.Select(attrs={"class": _daisy_select}),
            # Visually hidden: task_form.html draws it as a flag toggle button.
            "is_priority": forms.CheckboxInput(attrs={"class": "sr-only"}),
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        self.fields["name"].label = "Task name"
        self.fields["assigned_members"].label = "Assignees"
        self.fields["reviewers"].label = "Reviewers"
        self.fields["requires_review"].label = "Require review"
        self.fields["requires_review"].help_text = (
            "Each assignee's submission has to be signed off by a reviewer "
            "before the task counts as done."
        )
        self.fields["deadline"].label = "Due date"
        self.fields["is_priority"].label = "Mark as high priority"
        self.fields["container"].label = "Kanban column"
        self.fields["container"].required = True
        # Prompt text shown for the unselected (blank) choice.
        self.fields["container"].empty_label = "Select Kanban column"
        # Show only the container's name in the dropdown (e.g. "In Progress"),
        # not the model's default "{name} (project {id})" representation.
        self.fields["container"].label_from_instance = lambda obj: obj.name
        self._init_task_type(project)
        self._init_individual_dates()
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

    def _init_task_type(self, project):
        """Offer the CRediT roles as the task's type — on CRediT projects only.

        The type is what a contribution is recorded under when the task
        closes, so it is meaningless anywhere else: on a project tracking
        contributions any other way the field is dropped from the form
        entirely and the task keeps whatever it already has.
        """
        if project is None or not project.tracks_credit:
            self.fields.pop("contribution_type", None)
            return
        field = self.fields["contribution_type"]
        field.label = "Task type"
        field.help_text = (
            "The CRediT role this task's contributors are credited with "
            "when it is completed."
        )
        current = self.instance.contribution_type if self.instance.pk else None
        choices = credit_role_choices(current)
        # "Other" is ours rather than CRediT's, but a task that fits none of
        # the 14 roles needs somewhere to go, so it is always offered — last,
        # where the app's ordering puts the catch-all.
        if ContributionType.OTHER not in {value for value, _label in choices}:
            choices.append((ContributionType.OTHER, ContributionType.OTHER.label))
        field.choices = [("", "No task type")] + choices
        field.required = False
        # A new task still opens on "No task type". The field inherits the
        # model default, "Other", which is now in the list and would
        # otherwise come up preselected; both save as "Other".
        if not self.instance.pk and "contribution_type" not in self.initial:
            field.initial = ""

    def _init_individual_dates(self):
        """Offer each individual-dates choice when editing a task some members
        have that kind of own date on; drop the field otherwise.

        ``individual_rows[field]`` lists ``(member, own date)`` for the panel.
        Shifting is preselected where it can apply (the task has that date
        now, so there is an amount to shift by); otherwise keeping them is.
        """
        entries = (
            list(
                self.instance.assignee_deadlines.select_related("member")
                .prefetch_related("member__users")
                .order_by("id")
            )
            if self.instance.pk
            else []
        )
        self.original_dates = {}
        self.individual_rows = {}
        for date_field, choice_field in self.INDIVIDUAL_DATE_CHOICES.items():
            original = getattr(self.instance, date_field) if self.instance.pk else None
            self.original_dates[date_field] = original
            rows = sorted(
                (
                    (e.member, getattr(e, date_field))
                    for e in entries
                    if getattr(e, date_field) is not None
                ),
                key=lambda row: row[1],
            )
            self.individual_rows[date_field] = rows
            if not rows:
                self.fields.pop(choice_field, None)
                continue
            field = self.fields[choice_field]
            field.label = (
                "Individual start dates" if date_field == "start_date" else "Individual deadlines"
            )
            if choice_field not in self.initial:
                field.initial = "shift" if original else "keep"

    # Read by the template and the view.
    @property
    def original_start_date(self):
        return self.original_dates.get("start_date")

    @property
    def original_deadline(self):
        return self.original_dates.get("deadline")

    @property
    def individual_start_rows(self):
        return self.individual_rows.get("start_date", [])

    @property
    def individual_deadline_rows(self):
        return self.individual_rows.get("deadline", [])

    @staticmethod
    def _to_minute(value):
        return value.replace(second=0, microsecond=0) if value else None

    def date_changed(self, date_field: str) -> bool:
        """Whether this edit moves, sets or clears the task's ``date_field``.

        Compared to the minute, the picker's precision, so a date stored
        with seconds is not "changed" by a re-save.
        """
        if not hasattr(self, "cleaned_data") or date_field not in self.cleaned_data:
            return False
        return self._to_minute(self.cleaned_data[date_field]) != self._to_minute(
            self.original_dates.get(date_field)
        )

    def changed_individual_dates(self):
        """``(date field, choice)`` for each task date this edit moved that
        some members have their own of — what the view carries over."""
        return [
            (date_field, self.cleaned_data.get(choice_field) or "keep")
            for date_field, choice_field in self.INDIVIDUAL_DATE_CHOICES.items()
            if choice_field in self.fields and self.date_changed(date_field)
        ]

    def clean_contribution_type(self):
        """An unpicked task type falls back to the model's own default."""
        return self.cleaned_data.get("contribution_type") or ContributionType.OTHER

    def clean(self):
        """A task cannot be due before it starts, and review needs a reviewer.

        The date rule matches ``MilestoneForm`` below; equal dates are fine,
        and either date on its own is unconstrained.

        Requiring review with nobody named to give it would leave a task
        nobody could finish, so the two are kept together: ticking the box
        asks for at least one reviewer, and leaving it unticked clears any
        who were named before.
        """
        cleaned = super().clean()
        start = cleaned.get("start_date")
        deadline = cleaned.get("deadline")
        if start and deadline and start > deadline:
            self.add_error(
                "start_date", "Start date must be before or equal to the due date."
            )

        # Moving the start or due date of a task some members have their own
        # of asks the Lead what happens to those, every time.
        names = {"start_date": "start date", "deadline": "deadline"}
        task_names = {"start_date": "start date", "deadline": "due date"}
        for date_field, choice_field in self.INDIVIDUAL_DATE_CHOICES.items():
            if choice_field not in self.fields or not self.date_changed(date_field):
                continue
            choice = cleaned.get(choice_field)
            new_value = cleaned.get(date_field)
            if not choice:
                self.add_error(
                    choice_field,
                    f"Some assignees have their own {names[date_field]}. Choose "
                    "whether to shift, keep or remove them.",
                )
            elif choice == "shift" and not (new_value and self.original_dates[date_field]):
                self.add_error(
                    choice_field,
                    "There is no change of date to shift by: the task "
                    + (
                        f"had no {task_names[date_field]}."
                        if new_value
                        else f"no longer has a {task_names[date_field]}."
                    )
                    + f" Keep or remove the individual {names[date_field]}s instead.",
                )

        if cleaned.get("requires_review"):
            if not cleaned.get("reviewers"):
                self.add_error(
                    "reviewers",
                    "Pick at least one reviewer, or untick Require review.",
                )
        else:
            cleaned["reviewers"] = self.fields["reviewers"].queryset.none()
        return cleaned


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


class AssigneeDatesForm(forms.Form):
    """A Lead's own start date and/or deadline for one assignee, or for every
    assignee not yet done (``views.TaskMemberDatesView``).

    Each date may be earlier or later than the task's. For one member
    (``scope="member"``) the dialog shows their current own dates, so an
    empty date means "follow the task's" and both empty removes them. For
    everyone not yet done (``scope="pending"``) an empty date means "leave
    each member's as it is", so at least one is needed.

    A deadline has to be in the future when it is being changed: one already
    stored, and passed, may be sent back untouched alongside a new start
    date. A start date must not fall after the deadline the member ends up
    with.
    """

    start_date = forms.DateTimeField(
        required=False,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"],
    )
    deadline = forms.DateTimeField(
        required=False,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"],
    )
    reason = forms.CharField(required=False, max_length=500)

    def __init__(self, *args, task, scope="member", current=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.task = task
        self.scope = scope
        self.current = current  # the member's AssigneeDeadline, if any

    @staticmethod
    def _minute(value):
        return value.replace(second=0, microsecond=0) if value else None

    def clean(self):
        from django.utils import timezone

        cleaned = super().clean()
        if self.errors:
            return cleaned
        start = cleaned.get("start_date")
        deadline = cleaned.get("deadline")
        if self.scope == "pending" and start is None and deadline is None:
            raise forms.ValidationError("Choose a start date or a deadline.")
        stored = self.current.deadline if self.current else None
        if (
            deadline is not None
            and self._minute(deadline) != self._minute(stored)
            and deadline <= timezone.now()
        ):
            self.add_error("deadline", "Choose a deadline in the future.")
        # The member's resulting dates: an empty one follows the task's (or,
        # for everyone not done, the task's is the best common reference).
        effective_start = start or self.task.start_date
        effective_deadline = deadline or self.task.deadline
        if effective_start and effective_deadline and effective_start > effective_deadline:
            self.add_error(
                "start_date",
                "The start date must be on or before the deadline ("
                + timezone.localtime(effective_deadline).strftime("%b %-d, %Y %-I:%M %p")
                + ").",
            )
        return cleaned

