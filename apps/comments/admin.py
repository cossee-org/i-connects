from django.contrib import admin

from .models import Comment, CommentReadStatus


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("id", "author", "task_log", "created_at", "updated_at")
    search_fields = ("content", "author__name")


@admin.register(CommentReadStatus)
class CommentReadStatusAdmin(admin.ModelAdmin):
    list_display = ("id", "comment", "project_member", "read", "created_at")
    list_filter = ("read",)
