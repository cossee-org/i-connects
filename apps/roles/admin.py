from django.contrib import admin

from .models import Role


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "user", "project", "taxonomy")
    search_fields = ("name", "description", "taxonomy", "user__email", "project__name")
    list_filter = ("taxonomy",)
