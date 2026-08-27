"""Views for the notifications app (full pages and HTMX partials)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.core.enums import UserRole
from apps.projects.models import ProjectMember

from .forms import AnnouncementForm
from .models import Notification
from .services import create_notification


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_admin(user) -> bool:
    if not user.is_authenticated:
        return False
    return bool(
        user.is_superuser
        or user.is_staff
        or getattr(user, "role", None) == UserRole.ADMIN
    )


# ---------------------------------------------------------------------------
# Full-page list
# ---------------------------------------------------------------------------
@login_required
@require_GET
def notification_list(request):
    # ``?filter=unread`` backs the "Unread" tab the template renders next to
    # "All"; anything else (including no value) falls through to everything.
    current_filter = request.GET.get("filter") or "all"
    qs = Notification.objects.for_user(request.user)
    if current_filter == "unread":
        qs = qs.filter(read=False)
    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "notifications/list.html",
        {
            "page_obj": page,
            "notifications": page.object_list,
            "current_filter": current_filter,
            "unread_count": Notification.objects.unread_for_user(
                request.user
            ).count(),
        },
    )


# ---------------------------------------------------------------------------
# HTMX partials
# ---------------------------------------------------------------------------
@login_required
@require_GET
def notification_badge(request):
    # Always renders, including at zero: the partial is a pair of
    # out-of-band swaps, so an empty response would leave a stale count
    # sitting in the chrome after the last notification is read elsewhere.
    count = Notification.objects.unread_for_user(request.user).count()
    return render(request, "notifications/badge.html", {"unread_count": count})


@login_required
@require_GET
def notification_dropdown(request):
    qs = Notification.objects.for_user(request.user)[:10]
    unread_count = Notification.objects.unread_for_user(request.user).count()
    return render(
        request,
        "notifications/dropdown.html",
        # Opening the panel is also a chance to re-sync the two badges.
        {
            "notifications": qs,
            "unread_count": unread_count,
            "swap_oob_badge": True,
        },
    )


@login_required
@require_POST
def mark_read(request, pk: int):
    notification = get_object_or_404(
        Notification.objects.for_user(request.user), pk=pk
    )
    if not notification.read:
        notification.read = True
        notification.save(update_fields=["read"])
    unread_count = Notification.objects.unread_for_user(request.user).count()
    return render(
        request,
        "notifications/item.html",
        {
            "notification": notification,
            "swap_oob_badge": True,
            "unread_count": unread_count,
        },
    )


@login_required
@require_POST
def mark_all_read(request):
    Notification.objects.for_user(request.user).filter(read=False).update(read=True)
    qs = Notification.objects.for_user(request.user)[:10]
    return render(
        request,
        "notifications/dropdown.html",
        {"notifications": qs, "unread_count": 0, "swap_oob_badge": True},
    )


# ---------------------------------------------------------------------------
# Announcements (admin only)
# ---------------------------------------------------------------------------
@login_required
@user_passes_test(_is_admin)
def announcement_create(request):
    form = AnnouncementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        project = form.cleaned_data.get("project")
        title = form.cleaned_data["title"]
        body = form.cleaned_data["body"]
        link = form.cleaned_data.get("link") or None

        User = get_user_model()
        if project is None:
            recipients = list(User.objects.filter(is_active=True))
        else:
            member_user_ids = (
                ProjectMember.objects.filter(project=project, deleted=False)
                .values_list("users__id", flat=True)
            )
            unique_ids = {i for i in member_user_ids if i}
            recipients = list(User.objects.filter(pk__in=unique_ids))

        create_notification(
            users=recipients,
            title=title,
            body=body,
            project=project,
            link=link,
            announcement=True,
        )
        messages.success(
            request, f"Announcement sent to {len(recipients)} user(s)."
        )
        return redirect("notifications:list")
    return render(
        request, "notifications/announcement_form.html", {"form": form}
    )
