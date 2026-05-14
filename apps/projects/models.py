"""Projects app: Project and its membership / invitation / widget scaffolding."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.enums import JoinRequestStatus, MemberPrivileges, ProjectStatus, WidgetSize


class Project(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Metadata
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    abstract = models.TextField(null=True, blank=True)
    keywords = models.TextField(null=True, blank=True)
    citation = models.TextField(null=True, blank=True)
    publisher = models.CharField(max_length=255, null=True, blank=True)
    identifier = models.CharField(max_length=255, null=True, blank=True)

    metadata = models.JSONField(null=True, blank=True)

    # Project form template (optional)
    form_version = models.ForeignKey(
        "forms_builder.FormVersion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="project_defaults",
    )

    status = models.CharField(
        max_length=32,
        choices=ProjectStatus.choices,
        default=ProjectStatus.PLANNING,
    )
    is_public = models.BooleanField(default=True)
    public_summary = models.TextField(null=True, blank=True)

    discord_webhook_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="Optional Discord webhook URL. Task approvals will post to this channel.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status"])]

    def __str__(self) -> str:
        return self.name


class ProjectMember(models.Model):
    """A membership (individual or team-shaped) on a project."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="project_members",
    )
    name = models.CharField(max_length=255, null=True, blank=True)
    users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="project_memberships",
        blank=True,
    )
    deleted = models.BooleanField(default=False)
    tags = models.JSONField(null=True, blank=True)
    former_team_ids = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["project"])]

    def __str__(self) -> str:
        return self.name or f"ProjectMember #{self.pk}"


class ProjectPrivilege(models.Model):
    """Per-user privilege assignment for a project."""

    created_at = models.DateTimeField(auto_now_add=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_privileges",
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="privileges",
    )
    privilege = models.CharField(
        max_length=32,
        choices=MemberPrivileges.choices,
        default=MemberPrivileges.PROJECT_MANAGER,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["project"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} / {self.project_id} ({self.privilege})"


class Invitation(models.Model):
    """Outstanding invitation to join a project."""

    created_at = models.DateTimeField(auto_now_add=True)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    invitation_code = models.CharField(max_length=255)
    roles = models.ManyToManyField(
        "roles.Role",
        blank=True,
        related_name="invitations",
    )
    email = models.EmailField(max_length=254)
    # Pre-resolved invitee, when a PM picks an existing user rather than
    # typing a free-text address. ``email`` is still populated (from the
    # user's account) so the accept/decline flow keeps working unchanged.
    invited_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_invitations",
    )
    privilege = models.CharField(
        max_length=32,
        choices=MemberPrivileges.choices,
        default=MemberPrivileges.CONTRIBUTOR,
    )
    added_by = models.CharField(max_length=255)
    tags = models.JSONField(null=True, blank=True)

    # When reassigning a former member's workload to a new invitee.
    reassignment_for = models.ForeignKey(
        ProjectMember,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reassignment_invitations",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project"]),
            models.Index(fields=["email"]),
        ]

    def __str__(self) -> str:
        return f"Invitation to {self.email} for project {self.project_id}"


class Element(models.Model):
    """A structural element within a project (e.g. a sub-study or component)."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="elements",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)

    # Prisma models Element parent/child as a many-to-many self relation.
    parents = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="children",
    )

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["project"])]

    def __str__(self) -> str:
        return self.name


class ProjectWidget(models.Model):
    """Per-user, per-project dashboard widget configuration."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_widgets",
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="widgets",
    )
    type = models.CharField(max_length=64)
    show = models.BooleanField(default=True)
    position = models.IntegerField()
    size = models.CharField(
        max_length=16,
        choices=WidgetSize.choices,
        default=WidgetSize.MEDIUM,
    )
    # Prisma declares this as MemberPrivileges[]; store as JSON list of enum values.
    privilege = models.JSONField(
        default=list,
        help_text="List of MemberPrivileges values that can see this widget.",
    )

    class Meta:
        ordering = ["position"]
        unique_together = [["user", "project", "type"]]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["project"]),
        ]

    def __str__(self) -> str:
        return f"{self.type} widget (u={self.user_id}, p={self.project_id})"


class JoinRequest(models.Model):
    """A user's request to join a project at a specific privilege level."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="join_requests",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_join_requests",
    )
    requested_privilege = models.CharField(
        max_length=32,
        choices=MemberPrivileges.choices,
        default=MemberPrivileges.CONTRIBUTOR,
    )
    message = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=JoinRequestStatus.choices,
        default=JoinRequestStatus.PENDING,
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decided_join_requests",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "user"],
                condition=models.Q(status="PENDING"),
                name="uniq_pending_join_request_per_user_project",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self) -> str:
        return f"JoinRequest #{self.pk} ({self.user_id} → {self.project_id}, {self.status})"
