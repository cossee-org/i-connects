from django.contrib import admin

from .models import WikiPage, WikiRevision


class WikiRevisionInline(admin.TabularInline):
    model = WikiRevision
    extra = 0
    fields = ("number", "title", "summary", "editor", "created_at")
    readonly_fields = fields
    # Revisions are append-only; the admin must not offer to rewrite one.
    can_delete = False

    def has_add_permission(self, request, obj):
        return False


@admin.register(WikiPage)
class WikiPageAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "project", "locked", "updated_by", "updated_at")
    list_filter = ("locked",)
    search_fields = ("title", "slug", "content_markdown", "project__name")
    readonly_fields = ("slug", "created_at", "updated_at")
    inlines = [WikiRevisionInline]


@admin.register(WikiRevision)
class WikiRevisionAdmin(admin.ModelAdmin):
    list_display = ("id", "page", "number", "summary", "editor", "created_at")
    search_fields = ("page__title", "summary", "content_markdown")
    list_filter = ("created_at",)

    def has_change_permission(self, request, obj=None):
        return False
