from django.apps import AppConfig


class MailingListConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.mailing_list"
    label = "mailing_list"
    verbose_name = "Mailing list"
