"""Read-only admin surface for ContributionRecord.

Per Ultra Plan §8: even admins cannot edit or delete contribution records.
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
        "verified_by",
        "task",
    )
    list_filter = ("contribution_type", "project")
    search_fields = ("user__email", "user__name", "task__name", "project__name")
    readonly_fields = (
        "task",
        "task_log",
        "user",
        "project",
        "contribution_type",
        "verified_by",
        "closed_at",
        "created_at",
    )
    date_hierarchy = "closed_at"

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
