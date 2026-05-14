from django.contrib import admin

from .models import (
    Element,
    Invitation,
    JoinRequest,
    Project,
    ProjectMember,
    ProjectPrivilege,
    ProjectWidget,
)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "status", "is_public", "created_at")
    search_fields = ("name", "description", "abstract", "keywords")
    list_filter = ("status", "is_public")


@admin.register(ProjectMember)
class ProjectMemberAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "project", "deleted", "created_at")
    search_fields = ("name", "project__name")
    list_filter = ("deleted",)


@admin.register(ProjectPrivilege)
class ProjectPrivilegeAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "project", "privilege", "created_at")
    search_fields = ("user__email", "project__name")
    list_filter = ("privilege",)


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("id", "email", "project", "privilege", "added_by", "created_at")
    search_fields = ("email", "project__name", "invitation_code", "added_by")
    list_filter = ("privilege",)


@admin.register(JoinRequest)
class JoinRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "project",
        "user",
        "requested_privilege",
        "status",
        "created_at",
        "decided_at",
    )
    search_fields = ("project__name", "user__email", "user__name")
    list_filter = ("status", "requested_privilege")


@admin.register(Element)
class ElementAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "project", "created_at")
    search_fields = ("name", "description", "project__name")


@admin.register(ProjectWidget)
class ProjectWidgetAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "project", "type", "position", "size", "show")
    search_fields = ("type", "user__email", "project__name")
    list_filter = ("type", "size", "show")
