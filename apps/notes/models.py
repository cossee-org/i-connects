"""Notes app: project-scoped notes authored by project members."""

from __future__ import annotations

from django.db import models

from apps.core.enums import NoteVisibility


class Note(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="notes",
    )
    author = models.ForeignKey(
        "projects.ProjectMember",
        on_delete=models.CASCADE,
        related_name="notes",
    )

    title = models.CharField(max_length=255, null=True, blank=True)
    # Portable storage for search/export.
    content_markdown = models.TextField(null=True, blank=True)
    # Optional rich-state (e.g. Lexical) for editor fidelity.
    content_json = models.JSONField(null=True, blank=True)

    visibility = models.CharField(
        max_length=16,
        choices=NoteVisibility.choices,
        default=NoteVisibility.PRIVATE,
    )
    pinned = models.BooleanField(default=False)
    archived = models.BooleanField(default=False)
    tags = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-pinned", "-updated_at"]
        indexes = [
            models.Index(fields=["project", "author"], name="note_project_author_idx"),
            models.Index(fields=["author", "pinned"], name="note_author_pinned_idx"),
        ]

    def __str__(self) -> str:
        return self.title or f"Note #{self.pk}"
