from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"
    label = "audit"
    verbose_name = "Audit log"

    def ready(self) -> None:
        # Register signal handlers for auth events (login, logout, failed login).
        from . import signals  # noqa: F401
