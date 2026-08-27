"""Admin surface for ContributionRecord.

Editable, in step with the ledger itself: credit is a claim about who did
what, and a wrong claim has to be fixable. The fields a task's closure
derives — the task, the submission, the person, the project — stay
read-only, since changing those is withdrawing one record and adding
another rather than correcting this one.
"""

from __future__ import annotations

from django.contrib import admin

from .models import ContributionRecord


@admin.register(ContributionRecord)
class ContributionRecordAdmin(admin.ModelAdmin):
    list_display = (
        "closed_at",
        "user",
        "project",
        "contribution_type",
        "added_by_hand",
        "verified_by",
        "task",
    )
    list_filter = ("contribution_type", "added_by_hand", "project")
    search_fields = ("user__email", "user__name", "task__name", "project__name")
    readonly_fields = (
        "task",
        "task_log",
        "user",
        "project",
        "added_by_hand",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "closed_at"

    def has_add_permission(self, request) -> bool:
        """Adding belongs on the project's own Contribution Tracking tab.

        A record needs a task, a person and a project that agree with each
        other; the form there picks them together, and this one would let
        them be typed in separately.
        """
        return False
