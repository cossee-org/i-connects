# Accessibility checklist (WCAG 2.1 AA)

Ultra Plan §7 makes WCAG 2.1 AA a hosting requirement. This guide is the sweep protocol for every template in `templates/`. `base.html` is already audited and adds the skip-link, focus-visible outline, `role` landmarks, `aria-current` on active tabs, and `aria-live` on messages — do not reapply those.

---

## The 10 checks

For every template you touch, go through this list in order.

### 1. Every `<img>` has `alt=""` or meaningful alt text
- Decorative images: `alt=""` (do NOT omit the attribute).
- Informative images (user avatars, logos in content): meaningful alt text. Logos inside a link whose accessible name already says the same thing → `alt=""` plus `aria-label` on the link.
- `<img>` inside a link with no text content → the link needs `aria-label`, and the image gets `alt=""`.

### 2. Every icon-only button / link has `aria-label`
- `<button>` containing only an `<svg>` needs `aria-label="..."`.
- Same for `<a>` that contains only an icon.
- SVG icons: add `aria-hidden="true"` to the `<svg>` itself so it's not announced twice.
- If the button shows a visible label on wider screens (`hidden sm:inline`), still add `aria-label` because small-screen users lose the label.

### 3. Form labels
- Every form input must have a `<label>` associated by `for="id"`, OR be wrapped inside `<label>`.
- Labels produced by `_components/form_field.html` are already correct — do not regress them.
- Placeholder is NOT a label. Keep placeholders as hints, add real labels.
- Required fields: the red asterisk in `form_field.html` is decorative; the `required` attribute on the input is the accessible signal (and the partial already adds it).

### 4. Headings form a clean hierarchy
- One `<h1>` per page (already provided by `_components/page_header.html`).
- No skipped levels (`<h1>` → `<h3>` is a violation; use `<h2>` between them).
- Use heading tags for actual headings, not styling. If you need small-uppercase emphasis, use `<span class="text-xs uppercase tracking-wider text-brand-coral font-semibold">` NOT `<h4>` or `<h5>`.

### 5. Tables have real `<thead>` / `<th scope="col">`
- Data tables: wrap column headers in `<thead>`, each `<th>` must have `scope="col"`.
- Row headers (e.g. "Member" column in the contributions matrix): `<th scope="row">`.
- If a table is purely layout, convert to `<div>`. We don't have layout tables; don't introduce them.

### 6. Link text is meaningful on its own
- "Click here" / "read more" / bare icons are violations when screen-reader users navigate by link list.
- Prefer `<a>Members (4)</a>` over `<a>View</a>` near a "Members" header.
- If the link text must stay short, add `aria-label` with more context.

### 7. Colour is not the only signal
- Status pills already carry text labels (`_components/status_badge.html`). Don't reduce to coloured dots alone.
- Form errors: the red colour alone is insufficient — the `form_field.html` partial already prints the error text; do not delete it.

### 8. Keyboard-only paths
- All interactive elements must be focusable. Use `<button>` / `<a>`, not `<div onclick>`.
- Custom dropdowns (DaisyUI) are already keyboard-navigable via `tabindex="0"`. Don't remove the `tabindex`.
- Drag-and-drop (kanban): there's no keyboard alternative today. This is a known gap — do not try to fix it in this pass; flag it.

### 9. Dynamic content announces correctly
- HTMX swaps that insert new content on the page: if the content is transient (a toast, a flash), wrap the target in `role="status" aria-live="polite"`.
- The notification badge already polls silently; it should be `aria-live="polite"` when it becomes non-zero. (`base.html` already does this.)
- Modal dialogs: use `<dialog>` or add `role="dialog" aria-modal="true"` plus an `aria-labelledby` pointing at the title.

### 10. Language + page title
- `<html lang="en">` is set in `base.html`. Per-language pages beyond English are out of scope for v1.
- Each page must set `{% block title %}` with descriptive text that includes "i-CONNECTS" (most already do).

---

## What NOT to change

- **Do not rewrite the brand tokens or layouts.** This pass is about adding attributes, not restyling.
- **Do not touch `_components/*.html`.** They're already audited. If you think one is wrong, flag it in your report instead.
- **Do not add ARIA where semantic HTML already suffices.** `<button>` doesn't need `role="button"`. `<a>` doesn't need `role="link"`. Extra ARIA that contradicts native semantics is worse than none.
- **Do not add `tabindex` values > 0.** They break tab order. `tabindex="0"` is fine; `tabindex="-1"` is fine for programmatic focus targets (like `#main-content`).
- **Preserve all HTMX / Alpine / SortableJS attributes.** Add accessibility attributes alongside; never replace functional attributes.

---

## Quick smoke after editing

After your pass:
1. `.venv/bin/python manage.py check --settings=config.settings.test` → 0 issues.
2. `.venv/bin/pytest -q` → no regressions.
3. Grep your edited files for common violations:
   - `grep -E '<img ' templates/... | grep -v 'alt='` — every `<img>` should have `alt`.
   - `grep -E '<svg' templates/... | head` — spot-check that decorative SVGs have `aria-hidden="true"`.

Report which files you changed and which of the 10 checks applied. Do not over-report non-changes.
