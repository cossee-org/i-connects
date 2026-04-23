"""Populate a fresh database with a realistic demo scenario.

    python manage.py seed_demo

Creates three users (a PM, a contributor, a verifier), one project with a
kanban board, a handful of tasks spanning every contribution type, and
runs them through the complete→approve lifecycle so the contributions
page has real data to show. Safe to re-run: the command is idempotent
and uses ``get_or_create`` throughout.

Intended for evaluation and demos, NOT production. All accounts use the
password ``demopass``.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.core.enums import (
    ContributionType,
    MemberPrivileges,
    ProjectStatus,
    TaskStatus,
)
from apps.projects.models import ProjectMember, ProjectPrivilege
from apps.projects.services import create_project_with_owner
from apps.tasks.models import KanbanBoard, Task
from apps.tasks.services import approve_completion, complete_task

User = get_user_model()


DEMO_PROJECT_NAME = "Demo: bird song meta-analysis"
DEMO_PASSWORD = "demopass"  # noqa: S105 — demo data only

DEMO_USERS = [
    # (email, name, privilege)
    ("alice@iconnects.test", "Alice Chen (PM)", MemberPrivileges.PROJECT_MANAGER),
    ("bob@iconnects.test", "Bob Patel (contributor)", MemberPrivileges.CONTRIBUTOR),
    ("cara@iconnects.test", "Cara Nguyen (contributor)", MemberPrivileges.CONTRIBUTOR),
]

DEMO_TASKS = [
    # (name, assignee_email, contribution_type, approved?)
    ("Screen abstracts batch 1", "bob@iconnects.test", ContributionType.DATA_CURATION, True),
    ("Screen abstracts batch 2", "cara@iconnects.test", ContributionType.DATA_CURATION, True),
    ("Extract effect sizes", "bob@iconnects.test", ContributionType.DATA_CURATION, True),
    ("Validate 20% random sample", "cara@iconnects.test", ContributionType.VALIDATION, True),
    ("Draft methods section", "alice@iconnects.test", ContributionType.WRITING, True),
    ("Code preregistration", "alice@iconnects.test", ContributionType.PROJECT_ADMINISTRATION, True),
    # One open task and one pending-approval for realism on the dashboard widgets.
    ("Review R analysis script", "bob@iconnects.test", ContributionType.VALIDATION, None),   # not yet done
    ("Write discussion draft", "cara@iconnects.test", ContributionType.WRITING, False),       # done, awaiting approval (we will complete but not approve)
]


class Command(BaseCommand):
    help = (
        "Seed a demo project + users + tasks + contributions. "
        "Idempotent; safe to run multiple times. Not for production."
    )

    def handle(self, *args, **options) -> None:
        users_by_email = {}
        for email, name, _priv in DEMO_USERS:
            user, created = User.objects.get_or_create(
                email=email,
                defaults={"name": name, "username": email},
            )
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save()
                self.stdout.write(f"  + created user {email}")
            users_by_email[email] = user

        alice = users_by_email["alice@iconnects.test"]

        # Project — owned by Alice (who is auto-added as PM).
        project = (
            ProjectPrivilege.objects.filter(
                user=alice, project__name=DEMO_PROJECT_NAME
            )
            .select_related("project")
            .first()
        )
        if project is not None:
            project = project.project
            self.stdout.write(f"  . project '{DEMO_PROJECT_NAME}' already exists")
        else:
            project = create_project_with_owner(
                user=alice,
                form_data={
                    "name": DEMO_PROJECT_NAME,
                    "description": (
                        "A worked example showing a meta-analysis of urban bird song. "
                        "All data is synthetic."
                    ),
                    "status": ProjectStatus.ACTIVE,
                    "is_public": True,
                },
            )
            self.stdout.write(f"  + created project '{DEMO_PROJECT_NAME}'")

        # Add Bob and Cara as contributors.
        for email, _name, priv in DEMO_USERS:
            if email == alice.email:
                continue
            user = users_by_email[email]
            member, _ = ProjectMember.objects.get_or_create(project=project)
            member.users.add(user)
            ProjectPrivilege.objects.get_or_create(
                user=user, project=project, defaults={"privilege": priv}
            )

        # Kanban board column.
        column, _ = KanbanBoard.objects.get_or_create(
            project=project, name="Backlog", defaults={"container_order": 0}
        )

        # Tasks.
        alice_member = ProjectMember.objects.filter(project=project, users=alice).first()
        next_order = Task.objects.filter(container=column).count()
        for name, assignee_email, ctype, approved in DEMO_TASKS:
            if Task.objects.filter(project=project, name=name).exists():
                continue
            assignee_user = users_by_email[assignee_email]
            assignee_member = ProjectMember.objects.filter(
                project=project, users=assignee_user
            ).first()
            task = Task.objects.create(
                project=project,
                created_by=alice_member,
                container=column,
                container_task_order=next_order,
                name=name,
                contribution_type=ctype,
                status=TaskStatus.NOT_COMPLETED,
            )
            next_order += 1
            task.assigned_members.add(assignee_member)

            if approved is None:
                # Leave open — will show up under "My active tasks" for the assignee.
                continue

            log = complete_task(task=task, completed_by=assignee_member)
            if approved:
                approve_completion(task_log=log, approved=True, verifier_user=alice)
            # approved=False means we stop after completion — shows up in
            # Alice's "Pending your approval" widget.

            self.stdout.write(f"  + task '{name}' ({assignee_email}, {ctype})")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Demo data ready."))
        self.stdout.write("")
        self.stdout.write("Sign in as any of these to explore:")
        for email, name, priv in DEMO_USERS:
            self.stdout.write(f"  {email}  ({name})")
        self.stdout.write(f"  password: {DEMO_PASSWORD}")
        self.stdout.write("")
