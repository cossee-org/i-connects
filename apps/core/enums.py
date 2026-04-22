"""Shared enum choices ported from the Prisma schema."""

from __future__ import annotations

from django.db import models


class MemberPrivileges(models.TextChoices):
    PROJECT_MANAGER = "PROJECT_MANAGER", "Project manager"
    CONTRIBUTOR = "CONTRIBUTOR", "Contributor"


class ProjectStatus(models.TextChoices):
    PLANNING = "PLANNING", "Planning"
    LOOKING_FOR_COLLABORATORS = "LOOKING_FOR_COLLABORATORS", "Looking for collaborators"
    ACTIVE = "ACTIVE", "Active"
    PAUSED = "PAUSED", "Paused"
    COMPLETED = "COMPLETED", "Completed"


class TaskStatus(models.TextChoices):
    COMPLETED = "COMPLETED", "Completed"
    NOT_COMPLETED = "NOT_COMPLETED", "Not completed"


class CompletedAs(models.TextChoices):
    INDIVIDUAL = "INDIVIDUAL", "Individual"
    TEAM = "TEAM", "Team"


class AutoAssignNew(models.TextChoices):
    NONE = "NONE", "None"
    CONTRIBUTOR = "CONTRIBUTOR", "Contributor"
    TEAM = "TEAM", "Team"
    ALL = "ALL", "All"


class WidgetSize(models.TextChoices):
    SMALL = "SMALL", "Small"
    MEDIUM = "MEDIUM", "Medium"
    LARGE = "LARGE", "Large"


class EmailFrequency(models.TextChoices):
    NEVER = "NEVER", "Never"
    DAILY = "DAILY", "Daily"
    WEEKLY = "WEEKLY", "Weekly"


class NoteVisibility(models.TextChoices):
    PRIVATE = "PRIVATE", "Private"
    PM_ONLY = "PM_ONLY", "Project managers only"
    CONTRIBUTORS = "CONTRIBUTORS", "Contributors"


class UserRole(models.TextChoices):
    USER = "USER", "User"
    ADMIN = "ADMIN", "Admin"
