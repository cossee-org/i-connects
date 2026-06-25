"""URL routes for the projects app (namespace: ``projects``)."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    # Dashboard
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
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
        "projects/<int:pk>/contribution-tracking/",
        views.ContributionTrackingView.as_view(),
        name="contribution_tracking",
    ),
    path(
        "projects/<int:pk>/wiki/",
        views.WikiView.as_view(),
        name="wiki",
    ),
    path(
        "projects/<int:pk>/forms/",
        views.ProjectFormsView.as_view(),
        name="project_forms",
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
    path(
        "projects/<int:pk>/invite/search/",
        views.user_invite_search,
        name="invite_user_search",
    ),
    path(
        "projects/<int:pk>/invitation-requests/",
        views.ProjectInvitationListView.as_view(),
        name="project_invitations",
    ),
    path(
        "projects/<int:pk>/invitations/<int:invitation_pk>/cancel/",
        views.CancelInvitationView.as_view(),
        name="cancel_invitation",
    ),
    path("invites/", views.MyInvitationsView.as_view(), name="my_invitations"),
    path(
        "invites/<str:code>/",
        views.AcceptOrDeclineInviteView.as_view(),
        name="accept_or_decline",
    ),
    # Join requests
    path(
        "projects/<int:pk>/join/",
        views.RequestToJoinView.as_view(),
        name="request_to_join",
    ),
    path(
        "projects/<int:pk>/join-requests/",
        views.ProjectJoinRequestListView.as_view(),
        name="join_requests",
    ),
    path(
        "projects/<int:pk>/join-requests/<int:join_request_pk>/review/",
        views.ReviewJoinRequestView.as_view(),
        name="review_join_request",
    ),
    path(
        "projects/<int:pk>/join-requests/<int:join_request_pk>/approve/",
        views.ApproveJoinRequestView.as_view(),
        name="approve_join_request",
    ),
    path(
        "projects/<int:pk>/join-requests/<int:join_request_pk>/decline/",
        views.DeclineJoinRequestView.as_view(),
        name="decline_join_request",
    ),
    path(
        "joins/<int:join_request_pk>/cancel/",
        views.CancelJoinRequestView.as_view(),
        name="cancel_join_request",
    ),
    path(
        "joins/",
        views.MyJoinRequestsView.as_view(),
        name="my_join_requests",
    ),
    path(
        "joins/incoming/",
        views.IncomingJoinRequestsView.as_view(),
        name="incoming_join_requests",
    ),
    path(
        "my-projects/badge/",
        views.my_projects_badge,
        name="my_projects_badge",
    ),
]
