"""URL configuration for the wiki app.

``new/`` is declared before the ``<slug:slug>/`` routes: "new" is itself a
valid slug, so the literal has to win. ``services.RESERVED_SLUGS`` keeps a
page from ever being given that address.
"""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "wiki"

urlpatterns = [
    path(
        "projects/<int:project_pk>/wiki/",
        views.WikiPageListView.as_view(),
        name="list",
    ),
    path(
        "projects/<int:project_pk>/wiki/new/",
        views.WikiPageCreateView.as_view(),
        name="create",
    ),
    path(
        "projects/<int:project_pk>/wiki/<slug:slug>/",
        views.WikiPageDetailView.as_view(),
        name="detail",
    ),
    path(
        "projects/<int:project_pk>/wiki/<slug:slug>/edit/",
        views.WikiPageUpdateView.as_view(),
        name="update",
    ),
    path(
        "projects/<int:project_pk>/wiki/<slug:slug>/delete/",
        views.WikiPageDeleteView.as_view(),
        name="delete",
    ),
    path(
        "projects/<int:project_pk>/wiki/<slug:slug>/history/",
        views.WikiPageHistoryView.as_view(),
        name="history",
    ),
    path(
        "projects/<int:project_pk>/wiki/<slug:slug>/history/<int:number>/",
        views.WikiRevisionDetailView.as_view(),
        name="revision",
    ),
    path(
        "projects/<int:project_pk>/wiki/<slug:slug>/history/<int:number>/restore/",
        views.WikiRevisionRestoreView.as_view(),
        name="restore_revision",
    ),
]
