"""URL routes for the projects app (namespace: ``projects``)."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    # Project list & CRUD
    path("projects/", views.PublicProjectListView.as_view(), name="list"),
    path("projects/mine/", views.MyProjectListView.as_view(), name="my_list"),
    path("projects/new/", views.ProjectCreateView.as_view(), name="create"),
    path("projects/<int:pk>/", views.ProjectDetailView.as_view(), name="detail"),
    path("projects/<int:pk>/edit/", views.ProjectUpdateView.as_view(), name="update"),
    path(
        "projects/<int:pk>/delete/",
        views.ProjectDeleteView.as_view(),
        name="delete",
    ),
    # Members
    path(
        "projects/<int:pk>/members/",
        views.ProjectMemberListView.as_view(),
        name="members",
    ),
    path(
        "projects/<int:pk>/members/<int:member_pk>/privilege/",
        views.UpdateMemberPrivilegeView.as_view(),
        name="update_privilege",
    ),
    path(
        "projects/<int:pk>/members/<int:member_pk>/remove/",
        views.RemoveMemberView.as_view(),
        name="remove_member",
    ),
    # Invitations
    path(
        "projects/<int:pk>/invite/",
        views.InviteCreateView.as_view(),
        name="invite",
    ),
    path("invites/", views.MyInvitationsView.as_view(), name="my_invitations"),
    path(
        "invites/<str:code>/",
        views.AcceptOrDeclineInviteView.as_view(),
        name="accept_or_decline",
    ),
]
