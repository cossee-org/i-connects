"""Forms for the accounts app.

Kept intentionally close to Django's built-in auth forms so that password
validators configured in settings are respected. Fields are adapted to the
custom ``User`` model (email as the login field, plus ``name`` and the two
email-frequency preferences).
"""

from __future__ import annotations

from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

User = get_user_model()


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------
class SignupForm(forms.Form):
    """Create a new user account.

    Collects given/family name (given name optional), email with
    confirmation, password with confirmation, and an explicit Terms of
    Use / Privacy Policy acknowledgement.
    """

    given_name = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "input input-bordered w-full"}),
    )
    family_name = forms.CharField(
        max_length=255,
        min_length=1,
        widget=forms.TextInput(attrs={"class": "input input-bordered w-full"}),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"class": "input input-bordered w-full"}),
    )
    email_confirm = forms.EmailField(
        label="Confirm email",
        widget=forms.EmailInput(attrs={"class": "input input-bordered w-full"}),
    )
    password = forms.CharField(
        min_length=10,
        max_length=100,
        widget=forms.PasswordInput(attrs={"class": "input input-bordered w-full"}),
    )
    password_confirm = forms.CharField(
        max_length=100,
        label="Confirm password",
        widget=forms.PasswordInput(attrs={"class": "input input-bordered w-full"}),
    )
    agree_terms = forms.BooleanField(
        required=True,
        label="I agree to the Terms of Use and Privacy Policy",
        error_messages={
            "required": "You must agree to the Terms of Use and Privacy Policy.",
        },
        widget=forms.CheckboxInput(attrs={"class": "checkbox checkbox-primary"}),
    )

    def clean_email(self) -> str:
        email = self.cleaned_data["email"].lower().strip()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email already exists.")
        return email

    def clean_email_confirm(self) -> str:
        return self.cleaned_data["email_confirm"].lower().strip()

    def clean_password(self) -> str:
        pw = self.cleaned_data["password"]
        validate_password(pw)
        return pw

    def clean(self) -> dict:
        cleaned = super().clean()
        email = cleaned.get("email")
        email_confirm = cleaned.get("email_confirm")
        if email and email_confirm and email != email_confirm:
            self.add_error("email_confirm", "Email addresses do not match.")
        pw = cleaned.get("password")
        pw_confirm = cleaned.get("password_confirm")
        if pw and pw_confirm and pw != pw_confirm:
            self.add_error("password_confirm", "Passwords do not match.")
        return cleaned

    def save(self) -> User:
        email = self.cleaned_data["email"]
        given_name = (self.cleaned_data.get("given_name") or "").strip()
        family_name = self.cleaned_data["family_name"].strip()
        full_name = f"{given_name} {family_name}".strip()
        return User.objects.create_user(
            email=email,
            password=self.cleaned_data["password"],
            name=full_name,
            username=email,
            tos=True,
        )


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
class LoginForm(forms.Form):
    """Email + password login form."""

    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"class": "input input-bordered w-full"}),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": "input input-bordered w-full"}),
    )

    def __init__(self, request=None, *args, **kwargs) -> None:
        self.request = request
        self.user_cache: User | None = None
        super().__init__(*args, **kwargs)

    def clean(self) -> dict:
        cleaned = super().clean()
        email = cleaned.get("email")
        password = cleaned.get("password")
        if email and password:
            # Short-circuit BEFORE authenticate() so a locked-out account
            # cannot be distinguished from "invalid credentials" by timing.
            # Look up the user by email — ``authenticate`` with a locked
            # account would succeed (is_active is still True) but we want
            # to refuse before issuing a session.
            existing = User.objects.filter(email__iexact=email).first()
            if existing is not None and existing.locked_at is not None:
                raise ValidationError(
                    "This account is locked after too many failed sign-in "
                    "attempts. Check your email for an unlock link."
                )
            user = authenticate(self.request, username=email, password=password)
            if user is None:
                raise ValidationError("Invalid email or password.")
            if not user.is_active:
                raise ValidationError("This account is inactive.")
            self.user_cache = user
        return cleaned

    def get_user(self) -> User | None:
        return self.user_cache


# ---------------------------------------------------------------------------
# Password reset request (forgot password)
# ---------------------------------------------------------------------------
class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"class": "input input-bordered w-full"}),
    )

    def clean_email(self) -> str:
        return self.cleaned_data["email"].lower().strip()


# ---------------------------------------------------------------------------
# Set password (via reset link)
# ---------------------------------------------------------------------------
class SetPasswordForm(forms.Form):
    """Set a new password for a user who followed a valid reset link."""

    new_password = forms.CharField(
        min_length=10,
        max_length=100,
        widget=forms.PasswordInput(attrs={"class": "input input-bordered w-full"}),
    )
    new_password_confirm = forms.CharField(
        min_length=10,
        max_length=100,
        label="Confirm new password",
        widget=forms.PasswordInput(attrs={"class": "input input-bordered w-full"}),
    )

    def __init__(self, user: User, *args, **kwargs) -> None:
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self) -> dict:
        cleaned = super().clean()
        pw = cleaned.get("new_password")
        pw_confirm = cleaned.get("new_password_confirm")
        if pw and pw_confirm and pw != pw_confirm:
            self.add_error("new_password_confirm", "Passwords do not match.")
        if pw:
            try:
                validate_password(pw, self.user)
            except ValidationError as exc:
                self.add_error("new_password", exc)
        return cleaned

    def save(self) -> User:
        self.user.set_password(self.cleaned_data["new_password"])
        self.user.save(update_fields=["password"])
        return self.user


# ---------------------------------------------------------------------------
# Change password (logged-in user supplying current password)
# ---------------------------------------------------------------------------
class ChangePasswordForm(forms.Form):
    current_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": "input input-bordered w-full"}),
    )
    new_password = forms.CharField(
        min_length=10,
        max_length=100,
        widget=forms.PasswordInput(attrs={"class": "input input-bordered w-full"}),
    )
    new_password_confirm = forms.CharField(
        min_length=10,
        max_length=100,
        label="Confirm new password",
        widget=forms.PasswordInput(attrs={"class": "input input-bordered w-full"}),
    )

    def __init__(self, user: User, *args, **kwargs) -> None:
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self) -> str:
        current = self.cleaned_data["current_password"]
        if not self.user.check_password(current):
            raise ValidationError("Current password is incorrect.")
        return current

    def clean(self) -> dict:
        cleaned = super().clean()
        pw = cleaned.get("new_password")
        pw_confirm = cleaned.get("new_password_confirm")
        if pw and pw_confirm and pw != pw_confirm:
            self.add_error("new_password_confirm", "Passwords do not match.")
        if pw:
            try:
                validate_password(pw, self.user)
            except ValidationError as exc:
                self.add_error("new_password", exc)
        return cleaned

    def save(self) -> User:
        self.user.set_password(self.cleaned_data["new_password"])
        self.user.save(update_fields=["password"])
        return self.user


# ---------------------------------------------------------------------------
# Profile edit
# ---------------------------------------------------------------------------
class ProfileForm(forms.ModelForm):
    """Editable profile fields for the logged-in user.

    Email updates are allowed but enforce uniqueness case-insensitively.
    """

    class Meta:
        model = User
        fields = [
            "name",
            "email",
            "orcid",
            "email_project_activity_frequency",
            "email_overdue_task_frequency",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "email": forms.EmailInput(attrs={"class": "input input-bordered w-full"}),
            "orcid": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "email_project_activity_frequency": forms.Select(
                attrs={"class": "select select-bordered w-full"}
            ),
            "email_overdue_task_frequency": forms.Select(
                attrs={"class": "select select-bordered w-full"}
            ),
        }

    def clean_email(self) -> str:
        email = self.cleaned_data["email"].lower().strip()
        qs = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("Another account is already using this email.")
        return email
