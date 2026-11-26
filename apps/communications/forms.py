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

    API_KEY_HELP = (
        "On the bot's card in Zulip (Settings → Personal → Bots), click the key "
        "icon to copy it. It is 32 letters and numbers, for example "
        "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY. It is never shown here again."
    )

    api_key = forms.CharField(
        label="Bot API key",
        required=False,
        strip=True,
        help_text=API_KEY_HELP,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={
                "class": _INPUT,
                "autocomplete": "off",
                "placeholder": "32 letters and numbers",
            },
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
        # Every field says what to put in it and shows an example: most Leads
        # set this up once, from a Zulip screen they rarely visit.
        help_texts = {
            "site": (
                "The address you open Zulip at, without anything after the "
                "domain. For example: https://your-group.zulipchat.com"
            ),
            "bot_email": (
                "Shown on the bot's card in Zulip, under Settings → Personal → "
                "Bots. For example: iconnects-bot@your-group.zulipchat.com"
            ),
            "channel": (
                "The channel to post in, as Zulip names it. The # is optional. "
                "If the channel is private, subscribe the bot to it first. "
                "For example: coral-reef-lab"
            ),
            "topic": (
                "The topic inside that channel. Zulip starts it with the first "
                "message if it does not exist yet. For example: Reef survey 2026"
            ),
            "notify_tasks": (
                "For example: Bea Lund created the task Survey site B, "
                "assigned to Sam Chen, due 12 Dec 2026"
            ),
            "notify_deadlines": (
                "Posted once a day for open tasks due within 3 days and tasks "
                "that just went overdue. For example: Deadlines coming up, "
                "Survey site B, 12 Dec 2026, Sam Chen"
            ),
            "notify_members": "For example: Sam Chen joined Reef survey 2026",
            "notify_comments": (
                "The comment is quoted in full. For example: Sam Chen commented "
                "on Survey site B: Samples are in the freezer."
            ),
            "notify_wiki": (
                "Pages only Leads can see are never posted. For example: Bea "
                "Lund edited the wiki page Field protocol"
            ),
            "enabled": "Untick to pause every message and keep the settings.",
        }
        widgets = {
            "site": forms.URLInput(
                attrs={"class": _INPUT, "placeholder": "https://your-group.zulipchat.com"}
            ),
            "bot_email": forms.EmailInput(
                attrs={
                    "class": _INPUT,
                    "autocomplete": "off",
                    "placeholder": "iconnects-bot@your-group.zulipchat.com",
                }
            ),
            "channel": forms.TextInput(
                attrs={"class": _INPUT, "placeholder": "coral-reef-lab"}
            ),
            "topic": forms.TextInput(
                attrs={"class": _INPUT, "placeholder": "Reef survey 2026"}
            ),
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
        # instance: what ``destination_changed`` compares against.
        self._stored_destination = self._destination(self.instance)
        if self._stored_key:
            self.fields["api_key"].help_text = (
                "Leave blank to keep the current key. To change it, "
                + self.API_KEY_HELP[0].lower()
                + self.API_KEY_HELP[1:]
            )
            self.fields["api_key"].widget.attrs["placeholder"] = "Unchanged"

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
