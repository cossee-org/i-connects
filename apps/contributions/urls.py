"""URLs for the contributions app."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "contributions"

urlpatterns = [
    path(
        "contributions/",
        views.ContributionsView.as_view(),
        name="contributions",
    ),
    path(
        "my/contributions/",
        views.MyContributionsView.as_view(),
        name="my_contributions",
    ),
    path(
        "projects/<int:project_pk>/contributions/",
        views.ProjectContributionsView.as_view(),
        name="project_contributions",
    ),
    path(
        "projects/<int:project_pk>/contributions/add/",
        views.ContributionAddView.as_view(),
        name="contribution_add",
    ),
    path(
        "projects/<int:project_pk>/contributions/<int:pk>/edit/",
        views.ContributionEditView.as_view(),
        name="contribution_edit",
    ),
    path(
        "projects/<int:project_pk>/contributions/<int:pk>/remove/",
        views.ContributionRemoveView.as_view(),
        name="contribution_remove",
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
