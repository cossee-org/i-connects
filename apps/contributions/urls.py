"""URLs for the contributions app."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "contributions"

urlpatterns = [
    path(
        "projects/<int:project_pk>/contributions/",
        views.ProjectContributionsView.as_view(),
        name="project_contributions",
    ),
]
