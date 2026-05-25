"""Backfill ``given_name`` / ``family_name`` from the legacy ``name`` field.

Users created before 0003 added the split-name fields have ``name`` populated
("Jane Doe") but blank ``given_name`` / ``family_name``. Without this
migration, the profile form would render the name fields empty for those
users even though their signup payload included a family name.

Heuristic: last whitespace-separated token is the family name; everything
before it is the given name. Single-token names go to ``family_name`` to
match how the signup form treats it (family name is the required identity
anchor).
"""

from __future__ import annotations

from django.db import migrations


def backfill_split_names(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    to_update = []
    for user in User.objects.filter(given_name="", family_name="").exclude(name=""):
        parts = (user.name or "").split()
        if not parts:
            continue
        if len(parts) == 1:
            user.given_name = ""
            user.family_name = parts[0]
        else:
            user.family_name = parts[-1]
            user.given_name = " ".join(parts[:-1])
        to_update.append(user)
    if to_update:
        User.objects.bulk_update(to_update, ["given_name", "family_name"])


def noop_reverse(apps, schema_editor):
    """Reverse leaves the split fields populated — the source ``name`` is
    untouched so no data is lost. Safe to no-op."""


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_user_department_user_family_name_user_given_name_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_split_names, noop_reverse),
    ]
