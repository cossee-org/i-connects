"""Views for the accounts app.

Server-rendered, HTMX-friendly auth flows using Django's built-in session
authentication. ORCID social auth is handled by ``django-allauth`` (see
``apps.accounts.adapters`` for the link-if-logged-in behaviour).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views.decorators.http import require_POST
from django.views.generic import FormView, UpdateView

from apps.contributions.services import (
    get_user_contribution_counts_by_type,
    get_user_contributions,
)
from apps.core.enums import ContributionType

from .emails import send_password_reset_email, send_welcome_email
from .forms import (
    ChangePasswordForm,
    LoginForm,
    PasswordResetRequestForm,
    ProfileForm,
    SetPasswordForm,
    SignupForm,
)
from .tokens import password_reset_token

User = get_user_model()


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------
class SignupView(FormView):
    template_name = "accounts/signup.html"
    form_class = SignupForm
    success_url = reverse_lazy("core:home")

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if request.user.is_authenticated:
            return redirect(self.success_url)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form: SignupForm) -> HttpResponse:
        user = form.save()
        # Auto-login immediately after signup.
        login(self.request, user, backend="django.contrib.auth.backends.ModelBackend")
        # Best-effort welcome email; don't block signup on delivery errors.
        try:
            send_welcome_email(user, request=self.request)
        except Exception:  # noqa: BLE001 — email failures shouldn't break signup
            pass
        messages.success(self.request, "Welcome to i-connects!")
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------
class LoginView(FormView):
    template_name = "accounts/login.html"
    form_class = LoginForm
    success_url = reverse_lazy("core:home")

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if request.user.is_authenticated:
            return redirect(self.success_url)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self) -> dict:
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def form_valid(self, form: LoginForm) -> HttpResponse:
        user = form.get_user()
        login(self.request, user)
        # Honour ``?next=`` if present and safe (same-origin only).
        next_url = self.request.GET.get("next") or self.request.POST.get("next")
        if next_url and next_url.startswith("/") and not next_url.startswith("//"):
            return HttpResponseRedirect(next_url)
        return super().form_valid(form)


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    """POST-only logout. CSRF is enforced by Django's middleware."""
    logout(request)
    return redirect("/")


# ---------------------------------------------------------------------------
# Forgot / reset password
# ---------------------------------------------------------------------------
class PasswordResetRequestView(FormView):
    template_name = "accounts/password_reset_request.html"
    form_class = PasswordResetRequestForm
    success_url = reverse_lazy("accounts:password_reset_sent")

    def form_valid(self, form: PasswordResetRequestForm) -> HttpResponse:
        email = form.cleaned_data["email"]
        # Always return the same response regardless of whether the email
        # matches a user, to avoid account enumeration.
        user = User.objects.filter(email__iexact=email).first()
        if user is not None:
            try:
                send_password_reset_email(user, request=self.request)
            except Exception:  # noqa: BLE001 — swallow to avoid leaking info
                pass
        return super().form_valid(form)


def password_reset_sent(request: HttpRequest) -> HttpResponse:
    return render(request, "accounts/password_reset_sent.html")


def _user_from_uidb64(uidb64: str):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        return User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return None


class PasswordResetConfirmView(FormView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = SetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_done")

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        self.user = _user_from_uidb64(kwargs.get("uidb64", ""))
        self.token = kwargs.get("token", "")
        self.valid_link = bool(
            self.user is not None
            and password_reset_token.check_token(self.user, self.token)
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self) -> dict:
        kwargs = super().get_form_kwargs()
        # Always pass a User instance so the form's password validators run.
        kwargs["user"] = self.user or User()
        return kwargs

    def get_context_data(self, **kwargs) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["valid_link"] = self.valid_link
        return ctx

    def form_valid(self, form: SetPasswordForm) -> HttpResponse:
        if not self.valid_link:
            return self.render_to_response(self.get_context_data(form=form))
        user = form.save()
        login(self.request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(self.request, "Your password has been reset.")
        return super().form_valid(form)

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not self.valid_link:
            return self.render_to_response(self.get_context_data(form=self.get_form()))
        return super().post(request, *args, **kwargs)


def password_reset_done(request: HttpRequest) -> HttpResponse:
    return render(request, "accounts/password_reset_done.html")


# ---------------------------------------------------------------------------
# Change password (authenticated)
# ---------------------------------------------------------------------------
@method_decorator(login_required, name="dispatch")
class PasswordChangeView(FormView):
    template_name = "accounts/password_change.html"
    form_class = ChangePasswordForm
    success_url = reverse_lazy("accounts:profile")

    def get_form_kwargs(self) -> dict:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form: ChangePasswordForm) -> HttpResponse:
        user = form.save()
        # Keep the current session alive after the password hash changes.
        update_session_auth_hash(self.request, user)
        messages.success(self.request, "Password updated.")
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
class ProfileView(LoginRequiredMixin, UpdateView):
    template_name = "accounts/profile.html"
    form_class = ProfileForm
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None):  # noqa: D401
        return self.request.user

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["contribution_counts"] = get_user_contribution_counts_by_type(
            self.request.user
        )
        context["contribution_types"] = ContributionType.choices
        context["recent_contributions"] = get_user_contributions(self.request.user)[:10]
        context["contribution_total"] = sum(context["contribution_counts"].values())
        return context

    def form_valid(self, form: ProfileForm) -> HttpResponse:
        messages.success(self.request, "Profile updated.")
        return super().form_valid(form)
