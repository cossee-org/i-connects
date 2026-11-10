"""Forms for a project's Communications section."""

from __future__ import annotations

from django import forms

from apps.integrations import zulip
from apps.integrations.models import ZulipConnection

_INPUT = "input input-sm input-bordered w-full"
_CHECKBOX = "checkbox checkbox-sm"


class ZulipConnectionForm(forms.ModelForm):
    """Connect a project to a Zulip topic (Leads only).

    The API key is never sent back to the browser: when editing, leaving it
    blank keeps the one already stored.
    """

    api_key = forms.CharField(
        label="Bot API key",
        required=False,
        strip=True,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={"class": _INPUT, "autocomplete": "off"},
        ),
    )

    class Meta:
        model = ZulipConnection
        fields = [
            "site",
            "bot_email",
            "api_key",
            "channel",
            "topic",
            "notify_tasks",
            "notify_deadlines",
            "notify_members",
            "notify_comments",
            "notify_wiki",
            "enabled",
        ]
        labels = {
            "site": "Zulip organisation URL",
            "bot_email": "Bot email",
            "channel": "Channel",
            "topic": "Topic",
            "notify_tasks": "Tasks: created, assigned, done, reviewed, completed",
            "notify_deadlines": "Deadlines: a daily reminder of what is due soon",
            "notify_members": "People: someone joins the project",
            "notify_comments": "Comments on tasks",
            "notify_wiki": "Wiki pages created or edited",
            "enabled": "Post to Zulip",
        }
        help_texts = {
            "site": "For example https://your-group.zulipchat.com",
            "bot_email": "",
            "enabled": "",
        }
        widgets = {
            "site": forms.URLInput(attrs={"class": _INPUT, "placeholder": "https://your-group.zulipchat.com"}),
            "bot_email": forms.EmailInput(attrs={"class": _INPUT, "autocomplete": "off"}),
            "channel": forms.TextInput(attrs={"class": _INPUT}),
            "topic": forms.TextInput(attrs={"class": _INPUT}),
            "notify_tasks": forms.CheckboxInput(attrs={"class": _CHECKBOX}),
            "notify_deadlines": forms.CheckboxInput(attrs={"class": _CHECKBOX}),
            "notify_members": forms.CheckboxInput(attrs={"class": _CHECKBOX}),
            "notify_comments": forms.CheckboxInput(attrs={"class": _CHECKBOX}),
            "notify_wiki": forms.CheckboxInput(attrs={"class": _CHECKBOX}),
            "enabled": forms.CheckboxInput(attrs={"class": _CHECKBOX}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stored_key = self.instance.api_key if self.instance.pk else ""
        # As stored, before validation writes the posted values onto the
        # instance — what ``destination_changed`` compares against.
        self._stored_destination = self._destination(self.instance)
        if self._stored_key:
            self.fields["api_key"].help_text = "Leave blank to keep the current key."

    def clean_site(self):
        site = zulip.normalise_site(self.cleaned_data.get("site", ""))
        if not zulip.is_valid_site(site):
            allowed = ", ".join(zulip.allowed_host_suffixes())
            raise forms.ValidationError(
                f"Enter the https address of a Zulip organisation on {allowed}."
            )
        return site

    def clean_channel(self):
        # People paste the name as Zulip shows it, with its "#".
        return self.cleaned_data.get("channel", "").strip().lstrip("#").strip()

    def clean_topic(self):
        return self.cleaned_data.get("topic", "").strip()

    def clean_api_key(self):
        key = self.cleaned_data.get("api_key") or self._stored_key
        if not key:
            raise forms.ValidationError("Enter the bot's API key.")
        return key

    @staticmethod
    def _destination(connection) -> tuple:
        return (
            connection.site,
            connection.bot_email,
            connection.api_key,
            connection.channel,
            connection.topic,
        )

    def destination_changed(self) -> bool:
        """Whether this save points somewhere new (or is the first one), so
        the connection is worth proving with a message before it is kept.
        Call after ``is_valid()``."""
        if not self.instance.pk:
            return True
        return self._destination(self.instance) != self._stored_destination


class TeamMessageForm(forms.Form):
    """A message from a project member to the team's Zulip topic."""

    text = forms.CharField(
        label="Message",
        max_length=2000,
        widget=forms.Textarea(
            attrs={
                "class": "textarea textarea-sm textarea-bordered w-full",
                "rows": 3,
                "maxlength": "2000",
                "placeholder": "Write to the team…",
            }
        ),
    )
