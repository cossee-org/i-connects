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
    path(
        "projects/<int:project_pk>/contributions.csv",
        views.ProjectContributionsCSVView.as_view(),
        name="project_contributions_csv",
    ),
    path(
        "my/contributions.csv",
        views.MyContributionsCSVView.as_view(),
        name="my_contributions_csv",
    ),
]
