"""Read-only admin surface for AuditLog (per Ultra Plan §7 immutability)."""

from __future__ import annotations

from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "action", "actor", "entity_type", "entity_id", "project")
    list_filter = ("action", "entity_type")
    search_fields = (
        "actor__email",
        "actor__name",
        "entity_type",
        "entity_id",
        "project__name",
    )
    date_hierarchy = "timestamp"
    readonly_fields = (
        "actor",
        "action",
        "entity_type",
        "entity_id",
        "project",
        "metadata",
        "timestamp",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
