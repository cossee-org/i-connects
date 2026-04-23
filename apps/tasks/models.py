"""Tasks app: KanbanBoard, Milestone, Task and TaskLog (audit trail)."""

from __future__ import annotations

from django.db import models

from apps.core.enums import AutoAssignNew, CompletedAs, ContributionType, TaskStatus


class KanbanBoard(models.Model):
    """A Kanban column (container) for a project's tasks."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    name = models.CharField(max_length=255, default="To Do")
    container_order = models.IntegerField()
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="containers",
    )

    class Meta:
        ordering = ["container_order"]
        indexes = [models.Index(fields=["project"])]

    def __str__(self) -> str:
        return f"{self.name} (project {self.project_id})"


class Milestone(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="milestones",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    tags = models.JSONField(null=True, blank=True)
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)

    # Prisma models Milestone parent/child as a many-to-many self relation.
    parents = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="children",
    )

    class Meta:
        ordering = ["start_date", "id"]
        indexes = [models.Index(fields=["project"])]

    def __str__(self) -> str:
        return self.name


class Task(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    created_by = models.ForeignKey(
        "projects.ProjectMember",
        on_delete=models.CASCADE,
        related_name="created_tasks",
    )

    # Task metadata
    deadline = models.DateTimeField(null=True, blank=True)
    start_date = models.DateTimeField(null=True, blank=True)
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    tags = models.JSONField(null=True, blank=True)

    # Kanban coding
    container_task_order = models.IntegerField()
    container = models.ForeignKey(
        KanbanBoard,
        on_delete=models.CASCADE,
        related_name="tasks",
    )

    # Task relations
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    milestone = models.ForeignKey(
        Milestone,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
    )
    form_version = models.ForeignKey(
        "forms_builder.FormVersion",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tasks",
    )
    element = models.ForeignKey(
        "projects.Element",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
    )

    status = models.CharField(
        max_length=32,
        choices=TaskStatus.choices,
        default=TaskStatus.NOT_COMPLETED,
    )

    assigned_members = models.ManyToManyField(
        "projects.ProjectMember",
        blank=True,
        related_name="assigned_tasks",
    )
    roles = models.ManyToManyField(
        "roles.Role",
        blank=True,
        related_name="tasks",
    )

    auto_assign_new = models.CharField(
        max_length=16,
        choices=AutoAssignNew.choices,
        default=AutoAssignNew.NONE,
    )
    anonymous = models.BooleanField(default=False)

    contribution_type = models.CharField(
        max_length=32,
        choices=ContributionType.choices,
        default=ContributionType.OTHER,
        help_text=(
            "CRediT-aligned contribution type used when a ContributionRecord "
            "is created on task approval."
        ),
    )

    github_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="Optional link to a GitHub issue or pull request for this task.",
    )

    class Meta:
        ordering = ["container_task_order", "id"]
        indexes = [
            models.Index(fields=["project"]),
            models.Index(fields=["container"]),
            models.Index(fields=["milestone"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return self.name


class TaskLog(models.Model):
    """Audit trail of task assignment and completion events."""

    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=32,
        choices=TaskStatus.choices,
        default=TaskStatus.NOT_COMPLETED,
    )
    metadata = models.JSONField(null=True, blank=True)
    completed_as = models.CharField(
        max_length=16,
        choices=CompletedAs.choices,
        default=CompletedAs.INDIVIDUAL,
    )
    assigned_to = models.ForeignKey(
        "projects.ProjectMember",
        on_delete=models.CASCADE,
        related_name="task_logs_assigned",
    )
    completed_by = models.ForeignKey(
        "projects.ProjectMember",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="task_logs_completed",
    )
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="task_logs",
    )
    approved = models.BooleanField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["task"]),
            models.Index(fields=["assigned_to"]),
            models.Index(fields=["completed_by"]),
        ]

    def __str__(self) -> str:
        return f"TaskLog #{self.pk} (task {self.task_id})"
