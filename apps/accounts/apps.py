from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"

    def ready(self) -> None:
        # Import for side effects: registers post_save signal for default
        # Widget creation. noqa: F401 - imported for the side effect.
        from . import signals  # noqa: F401
