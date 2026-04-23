"""URL configuration for the mailing_list app."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "mailing_list"

urlpatterns = [
    path(
        "projects/<int:project_pk>/mailing-list/",
        views.MailingListArchiveView.as_view(),
        name="archive",
    ),
    path(
        "projects/<int:project_pk>/mailing-list/compose/",
        views.MailingListComposeView.as_view(),
        name="compose",
    ),
    path(
        "projects/<int:project_pk>/mailing-list/<int:pk>/",
        views.MailingListDetailView.as_view(),
        name="detail",
    ),
]
