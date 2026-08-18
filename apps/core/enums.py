"""Shared enum choices ported from the Prisma schema."""

from __future__ import annotations

from django.db import models


class MemberPrivileges(models.TextChoices):
    PROJECT_MANAGER = "PROJECT_MANAGER", "Lead"
    CONTRIBUTOR = "CONTRIBUTOR", "Member"


class JoinRequestStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    DECLINED = "DECLINED", "Declined"
    CANCELLED = "CANCELLED", "Cancelled"


class ProjectStatus(models.TextChoices):
    PLANNING = "PLANNING", "Planning"
    ACTIVE = "ACTIVE", "Active"
    PAUSED = "PAUSED", "Paused"
    COMPLETED = "COMPLETED", "Completed"
    INACTIVE = "INACTIVE", "Inactive"


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


class ContributionType(models.TextChoices):
    """CRediT-aligned contribution types — full NISO CRediT 1.0 taxonomy.

    See https://credit.niso.org/. The values are legacy-compatible: existing
    rows with ``WRITING`` map to "Writing – original draft" conceptually but
    are preserved verbatim (migrations do not rewrite user data).
    """

    # ---- v1 simplified subset (kept verbatim for backward compatibility) --
    INVESTIGATION = "INVESTIGATION", "Investigation"
    DATA_CURATION = "DATA_CURATION", "Data curation"
    VALIDATION = "VALIDATION", "Validation"
    WRITING = "WRITING", "Writing (legacy)"
    PROJECT_ADMINISTRATION = "PROJECT_ADMINISTRATION", "Project administration"
    OTHER = "OTHER", "Other"

    # ---- Full CRediT taxonomy (v2) ----------------------------------------
    CONCEPTUALIZATION = "CONCEPTUALIZATION", "Conceptualization"
    FORMAL_ANALYSIS = "FORMAL_ANALYSIS", "Formal analysis"
    FUNDING_ACQUISITION = "FUNDING_ACQUISITION", "Funding acquisition"
    METHODOLOGY = "METHODOLOGY", "Methodology"
    RESOURCES = "RESOURCES", "Resources"
    SOFTWARE = "SOFTWARE", "Software"
    SUPERVISION = "SUPERVISION", "Supervision"
    VISUALIZATION = "VISUALIZATION", "Visualization"
    WRITING_ORIGINAL_DRAFT = "WRITING_ORIGINAL_DRAFT", "Writing – original draft"
    WRITING_REVIEW_EDITING = "WRITING_REVIEW_EDITING", "Writing – review & editing"
