"""Read-only admin for the email log."""

from __future__ import annotations

from django.contrib import admin

from .models import EmailLog


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ("sent_at", "kind", "to_email", "project", "subject", "status")
    list_filter = ("kind", "status")
    search_fields = ("to_email", "recipient__name", "subject", "project__name")
    date_hierarchy = "sent_at"

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
