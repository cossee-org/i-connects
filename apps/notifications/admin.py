from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "message", "project", "read", "announcement", "created_at")
    search_fields = ("message", "project__name")
    list_filter = ("read", "announcement")
