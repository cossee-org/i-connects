from django.contrib import admin

from .models import ZulipConnection


@admin.register(ZulipConnection)
class ZulipConnectionAdmin(admin.ModelAdmin):
    list_display = ("project", "site", "channel", "topic", "enabled")
    list_filter = ("enabled",)
    search_fields = ("project__name", "channel", "topic")
    # The key is write-once from the project's Communications page.
    exclude = ("api_key",)
