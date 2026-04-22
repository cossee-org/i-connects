from django.contrib import admin

from .models import Note


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "project", "author", "visibility", "pinned", "archived", "updated_at")
    search_fields = ("title", "content_markdown", "project__name")
    list_filter = ("visibility", "pinned", "archived")
