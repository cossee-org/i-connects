"""URL configuration for the communications app."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "communications"

urlpatterns = [
    path(
        "communications/",
        views.CommunicationsView.as_view(),
        name="list",
    ),
    path(
        "projects/<int:project_pk>/communications/",
        views.ProjectCommunicationsView.as_view(),
        name="project",
    ),
]
