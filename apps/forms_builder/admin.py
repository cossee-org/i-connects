from django.contrib import admin

from .models import Form, FormVersion


@admin.register(Form)
class FormAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "archived", "created_at", "updated_at")
    search_fields = ("user__email",)
    list_filter = ("archived",)


@admin.register(FormVersion)
class FormVersionAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "form", "version", "created_at")
    search_fields = ("name", "form__user__email")
