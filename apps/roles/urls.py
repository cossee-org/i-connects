"""URL routes for the roles app (namespace: ``roles``)."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "roles"

urlpatterns = [
    path(
        "projects/<int:project_pk>/roles/",
        views.RoleListView.as_view(),
        name="list",
    ),
    path(
        "projects/<int:project_pk>/roles/new/",
        views.RoleCreateView.as_view(),
        name="create",
    ),
    path(
        "projects/<int:project_pk>/roles/<int:pk>/edit/",
        views.RoleUpdateView.as_view(),
        name="update",
    ),
    path(
        "projects/<int:project_pk>/roles/<int:pk>/delete/",
        views.RoleDeleteView.as_view(),
        name="delete",
    ),
    path(
        "projects/<int:project_pk>/members/<int:member_pk>/roles/",
        views.AssignRolesView.as_view(),
        name="assign_to_member",
    ),
]
