"""Forms builder app: user-authored Forms and their versioned schemas."""

from __future__ import annotations

from django.conf import settings
from django.db import models


class Form(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="forms",
    )
    archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["user"])]

    def __str__(self) -> str:
        return f"Form #{self.pk} by {self.user_id}"


class FormVersion(models.Model):
    """An immutable version of a Form's JSON/UI schema."""

    name = models.CharField(max_length=255)
    form = models.ForeignKey(
        Form,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    version = models.IntegerField()
    json_schema = models.JSONField(default=dict)
    ui_schema = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    archived = models.BooleanField(default=False)

    # Reverse relations:
    #   ``tasks``             <- Task.form_version FK
    #   ``project_defaults``  <- Project.form_version FK

    class Meta:
        ordering = ["form_id", "-version"]
        indexes = [
            models.Index(fields=["form", "version"], name="formVersionIndex"),
        ]

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"
