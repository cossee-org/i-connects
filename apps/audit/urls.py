"""URLs for the audit log viewer."""

from __future__ import annotations

from django.urls import path

from apps.audit import views

app_name = "audit"

urlpatterns = [
    path(
        "projects/<int:project_pk>/activity/",
        views.ProjectAuditView.as_view(),
        name="project_activity",
    ),
]
