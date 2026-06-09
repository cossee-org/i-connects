"""URL routing for the comments app (namespace ``comments``)."""

from __future__ import annotations

from django.urls import path

from apps.comments import views

app_name = "comments"

urlpatterns = [
    path(
        "tasks/<int:task_pk>/comments/",
        views.CommentCreateView.as_view(),
        name="create",
    ),
    path(
        "comments/<int:pk>/read/",
        views.CommentMarkReadView.as_view(),
        name="mark_read",
    ),
    path(
        "comments/<int:pk>/edit/",
        views.CommentEditView.as_view(),
        name="edit",
    ),
    path(
        "comments/<int:pk>/",
        views.CommentDeleteView.as_view(),
        name="delete",
    ),
]
