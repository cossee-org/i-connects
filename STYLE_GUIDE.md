# i&bull;CONNECTS Django Style Guide

Ported from the Ultra Plan (Section A) and STAPLE-dev's Tailwind theme. **Every template must follow this guide** so the UI stays consistent across apps.

---

## Brand tokens (DaisyUI theme name: `iconnects`)

| Token                 | Hex        | Where to use                                         |
| --------------------- | ---------- | ---------------------------------------------------- |
| `primary` / `brand-navy`    | `#323895`  | Headings, primary buttons, logo wordmark, links      |
| `accent` / `brand-coral`    | `#f03d30`  | CTAs, active tab underline, critical actions         |
| `base-300` / `brand-light`  | `#bdcbe8`  | Top navbar background, section dividers              |
| `base-200` / `brand-lighter`| `#eaeff8`  | Page background, secondary tabs, panels              |
| `base-100`                 | `#ffffff`  | Cards, table rows                                    |
| `brand-dark`               | `#231f20`  | Deep shadows or high-contrast small text only        |
| `brand-text`               | `#333333`  | Body copy                                            |

Font family: **Inter** (loaded from Google Fonts in `input.css`). Headings weight 600, body 400.

---

## Page shell (`base.html`)

Every page **extends `base.html`** and uses these blocks:

| Block            | Purpose                                                                 |
| ---------------- | ----------------------------------------------------------------------- |
| `title`          | `<title>` tag. End with `&mdash; i&bull;CONNECTS`                       |
| `breadcrumbs`    | Inline breadcrumb trail inside the top navbar                           |
| `brand_tabs`     | Override to hide the default secondary tab strip                        |
| `sidebar`        | Optional left sidebar (project or admin contexts)                       |
| `page_header`    | Title block above the content                                           |
| `content`        | Main page content                                                       |
| `main_wrapper`   | Override only if you need a full-bleed layout                           |
| `extra_head`, `extra_body` | Page-specific scripts/styles                                   |

**Do not duplicate the navbar, footer, or messages area in individual pages** — they are already in `base.html`.

---

## Reusable partials (in `templates/_components/`)

Use these rather than rewriting the markup each time:

```django
{# Page title + optional action button on the right #}
{% include "_components/page_header.html" with title="My Projects" subtitle="Projects you own or belong to." %}

{# Empty list / zero state #}
{% include "_components/empty_state.html" with title="No projects yet" body="Create one or ask to be invited." cta_href=new_url cta_label="Create project" %}

{# Status pill #}
{% include "_components/status_badge.html" with status=project.status %}

{# Form field with brand label + error styling #}
{% include "_components/form_field.html" with field=form.email %}
```

---

## Component rules

### Buttons
- **Primary CTA (the ONE action on the page):** `class="btn bg-brand-coral hover:bg-brand-coral/90 text-white border-0"`
- **Secondary / confirm:** `class="btn btn-primary"` (navy)
- **Neutral:** `class="btn btn-ghost"`
- **Destructive:** `class="btn btn-outline btn-error"`
- **Size:** default `btn-md`, `btn-sm` for inline/table row actions.
- **Never** use bright gradients, emoji, or rounded-pill buttons with gradients. Institutional clarity, not SaaS gamification.

### Cards / panels
- White surface: `class="brand-card p-6"` (= `bg-base-100 border border-brand-light/60 rounded-lg shadow-sm`)
- Lavender surface (for callouts, sidebars): `class="brand-panel p-6"`

### Tables (high-density lists — tasks, members, admin)
```html
<div class="brand-card overflow-hidden">
  <table class="table">
    <thead class="bg-brand-lighter text-brand-navy">
      <tr><th>Name</th><th>Status</th><th class="text-right">Actions</th></tr>
    </thead>
    <tbody>...</tbody>
  </table>
</div>
```

### Forms
- Use `{% include "_components/form_field.html" %}` for each field.
- For DaisyUI widget classes on form inputs, add them via the widget in the Django form: `attrs={"class": "input input-bordered w-full"}`.
- Grouped actions at the bottom: `<div class="flex gap-2 justify-end mt-6">` with cancel (ghost) on the left of the submit.

### Badges / status
- Always use `{% include "_components/status_badge.html" with status=obj.status %}` for lifecycle pills so colours are consistent across apps.

### Dropdowns / menus
- Use DaisyUI `dropdown` pattern with `dropdown-content menu bg-base-100 border border-brand-light rounded-box shadow`.

---

## Layout rules

- **Main content wrapper:** `container mx-auto px-4 sm:px-6 lg:px-8 py-8 max-w-7xl` (this is the default `main_wrapper` block — don't re-wrap).
- **Page header** goes in the `{% block page_header %}` so it sits above the content but inside the wrapper.
- **Use a single primary action per page.** No duplicate CTAs.
- **Breadcrumbs** belong in the top navbar (`{% block breadcrumbs %}`), not above the page header.

---

## Typography

- Page title: `text-2xl sm:text-3xl font-semibold text-brand-navy` (already in `page_header.html`).
- Section heading: `text-lg font-semibold text-brand-navy mb-3`.
- Body text: default (no class needed); muted text: `text-brand-text/70`.
- **Never** use `text-primary` on body paragraphs — that's reserved for headings and links.

---

## Do / Don't

| Do                                                          | Don't                                                                 |
| ----------------------------------------------------------- | --------------------------------------------------------------------- |
| Use coral `brand-coral` for the ONE primary CTA             | Scatter coral across multiple buttons on the same page                |
| Put page title in `{% block page_header %}`                 | Hard-code navbar or footer inside page templates                      |
| Use `brand-card` for white panels                           | Wrap everything in `bg-base-100 shadow-lg rounded-xl` manually        |
| Keep Tailwind classes in template markup                    | Add inline `style="..."` attributes                                   |
| Use existing HTMX attributes as-is                          | Remove `hx-*` or `data-*` attributes while restyling                  |
| Preserve all Django template logic (`{% %}`, `{{ }}`)       | Move or delete `{% csrf_token %}` / `{% url %}` tags                  |

---

## When in doubt

1. Look at `templates/base.html` and `templates/projects/my_list.html` as the reference.
2. Check if a `_components/*.html` partial exists for what you need.
3. Use the brand tokens (`brand-navy`, `brand-coral`, `brand-light`, `brand-lighter`) — **not** arbitrary Tailwind colours like `blue-500` or `rose-400`.
4. Keep the existing DOM structure where HTMX or Alpine.js logic depends on it. Add Tailwind classes; do not restructure.
