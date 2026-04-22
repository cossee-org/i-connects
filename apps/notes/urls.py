"""URL configuration for the notes app."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "notes"

urlpatterns = [
    path(
        "projects/<int:project_pk>/notes/",
        views.note_list,
        name="list",
    ),
    path(
        "projects/<int:project_pk>/notes/new/",
        views.note_create,
        name="create",
    ),
    path(
        "projects/<int:project_pk>/notes/<int:pk>/",
        views.note_detail,
        name="detail",
    ),
    path(
        "projects/<int:project_pk>/notes/<int:pk>/edit/",
        views.note_update,
        name="update",
    ),
    path(
        "projects/<int:project_pk>/notes/<int:pk>/delete/",
        views.note_delete,
        name="delete",
    ),
]
