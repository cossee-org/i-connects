# Audit log wire-up guide

The `apps.audit` app provides an append-only ledger of security- and governance-relevant events. The model, service, admin, and auth-signal handlers are already in place and tested. This guide tells wire-up agents which action constants to use at which call sites.

---

## The API

```python
from apps.audit.services import AuditAction, log_action

log_action(
    AuditAction.PROJECT_CREATED,   # required: enum or string
    actor=request.user,             # optional: User performing the action
    entity=project,                 # optional: model instance (derives entity_type / entity_id)
    project=project,                # optional: Project for project-scoped filtering
    metadata={"name": project.name},# optional: small JSON payload (do NOT include passwords / tokens)
)
```

Rules:

1. **Never log inside a `try` block that swallows its own errors.** Audit calls must be loud. If writing fails, the action should visibly fail.
2. **Never put secrets in `metadata`.** No passwords, no reset tokens, no API keys.
3. **Pass `project=` whenever an action is project-scoped** (task, invitation, member change, role change). The audit list views filter on this FK.
4. **Pass `entity=obj` when the action targets a specific row.** That populates `entity_type` and `entity_id` in one go.
5. **Log at the service layer when possible**, not inside every view. If there's no service function, log in the view.
6. **One action per side effect.** `PROJECT_CREATED` is one log row; `INVITATION_SENT` is another. Do not log a single compound action.

---

## Action constants

| Constant                              | When to emit                                                    |
| ------------------------------------- | --------------------------------------------------------------- |
| `PROJECT_CREATED`                     | New Project row committed                                       |
| `PROJECT_UPDATED`                     | Project edit form saved                                         |
| `PROJECT_DELETED`                     | Project hard-deleted (admin path)                               |
| `INVITATION_SENT`                     | Invitation row created and email queued                         |
| `INVITATION_ACCEPTED`                 | User accepts an invitation                                      |
| `INVITATION_DECLINED`                 | User declines an invitation                                     |
| `MEMBER_ADDED`                        | User added to a project membership                              |
| `MEMBER_REMOVED`                      | User removed from a project membership                          |
| `MEMBER_PRIVILEGE_CHANGED`            | Privilege flipped (PM ↔ CONTRIBUTOR)                            |
| `ROLE_CREATED`                        | Role defined on a project                                       |
| `ROLE_DELETED`                        | Role removed                                                    |
| `ROLE_ASSIGNED`                       | Role attached to a member                                       |
| `ROLE_UNASSIGNED`                     | Role detached                                                   |
| `TASK_CREATED`                        | Task row created                                                |
| `TASK_UPDATED`                        | Task edit form saved                                            |
| `TASK_DELETED`                        | Task deleted                                                    |
| `TASK_COMPLETED`                      | Task marked complete (status → COMPLETED)                       |
| `TASK_APPROVED`                       | `approve_completion(approved=True)`                             |
| `TASK_REJECTED`                       | `approve_completion(approved=False)`                            |
| `WIKI_PAGE_CREATED`                   | Wiki page created (with its first revision)                     |
| `WIKI_PAGE_UPDATED`                   | Wiki page saved with a change (text and/or lock)                |
| `WIKI_PAGE_DELETED`                   | Wiki page and its history deleted                               |
| `WIKI_PAGE_RESTORED`                  | An earlier revision restored onto the page                      |
| Already wired:                        |                                                                 |
| `USER_SIGNUP`, `USER_LOGIN`, `USER_LOGIN_FAILED`, `USER_LOGOUT`, `PASSWORD_*`, `PROFILE_UPDATED` | — |

---

## Suggested `metadata` keys (keep them short and stable)

- **Project actions:** `{"name": project.name}`
- **Task actions:** `{"name": task.name}` (also consider `{"status": "COMPLETED"}` for transitions)
- **Invitation actions:** `{"email": invitation.email}`
- **Member / role:** `{"target_user_id": user.id}` — use IDs not full names, so logs stay compact
- **Wiki actions:** `{"title": page.title, "slug": page.slug}` — the slug outlives a rename, so a deleted page is still identifiable

---

## Where NOT to log

- **Read operations** (list / detail / HTMX GETs). The audit log is for *changes*.
- **Bulk imports / cron jobs** unless specifically called out.
- **HTMX partial refreshes** of data you already logged on the underlying change.

---

## Testing the wire-up

After inserting a `log_action(...)` call, add a one-line test that the row exists:

```python
from apps.audit.models import AuditAction, AuditLog

# in an existing test after the action runs:
assert AuditLog.objects.filter(action=AuditAction.PROJECT_CREATED, actor=user).exists()
```

Do not add more than one or two tests per wire-up — the ledger itself is covered in `apps/audit/tests.py`.
