from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.integrations"
    label = "integrations"

    def ready(self) -> None:
        # Registers the receivers that post project activity to Zulip.
        from . import signals  # noqa: F401
