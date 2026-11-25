"""Template tags for the i-CONNECTS email templates.

Every email extends ``templates/emails/base_email.html`` and builds its body
from the parts in ``templates/emails/_parts/``. Those parts and the base are
the only email templates that carry colours, fonts or inline styles, so the
look of every email is changed in one place.

``{% emailpart %}`` renders a part around its inner content::

    {% load email_tags %}
    {% emailpart "button" href=url %}Open the task{% endemailpart %}
    {% emailpart "quote" %}{{ excerpt }}{% endemailpart %}

The inner content reaches the part as ``content``, and each keyword
argument under its own name.

``{% site_url %}`` and ``{% email_static %}`` build absolute links: email
is read away from the site, so a relative link goes nowhere.
"""

from __future__ import annotations

from django import template
from django.conf import settings
from django.template.loader import get_template
from django.urls import NoReverseMatch, reverse
from django.utils.safestring import mark_safe

from apps.core.emails import absolute_url

register = template.Library()

PARTS_DIR = "emails/_parts"


class EmailPartNode(template.Node):
    def __init__(self, name, kwargs, nodelist):
        self.name = name
        self.kwargs = kwargs
        self.nodelist = nodelist

    def render(self, context):
        name = self.name.resolve(context)
        part = get_template(f"{PARTS_DIR}/{name}.html")
        values = {key: value.resolve(context) for key, value in self.kwargs.items()}
        # The inner content was rendered (and escaped) by this same template,
        # so it is already safe to place as-is.
        values["content"] = mark_safe(self.nodelist.render(context).strip())
        with context.push(**values):
            # Parts are snippets: trailing newlines in their files must not
            # leak into inline contexts such as a link inside a sentence.
            return part.template.render(context).strip()


@register.tag
def emailpart(parser, token):
    """``{% emailpart "name" key=value ... %}inner{% endemailpart %}``."""
    bits = token.split_contents()
    if len(bits) < 2:
        raise template.TemplateSyntaxError(
            "emailpart needs a part name, e.g. {% emailpart \"button\" href=url %}"
        )
    name = parser.compile_filter(bits[1])
    kwargs = {}
    for bit in bits[2:]:
        key, sep, value = bit.partition("=")
        if not sep or not key:
            raise template.TemplateSyntaxError(
                f"emailpart arguments must be key=value, got {bit!r}"
            )
        kwargs[key] = parser.compile_filter(value)
    nodelist = parser.parse(("endemailpart",))
    parser.delete_first_token()
    return EmailPartNode(name, kwargs, nodelist)


@register.simple_tag
def site_url(route: str = "", *args, **kwargs) -> str:
    """The absolute URL of a named route, or of the site's home page.

    ``{% site_url "accounts:profile" %}``. A route that does not reverse
    falls back to the home page rather than breaking the email.
    """
    path = "/"
    if route:
        try:
            path = reverse(route, args=args or None, kwargs=kwargs or None)
        except NoReverseMatch:
            path = "/"
    return absolute_url(path)


@register.simple_tag
def email_static(path: str) -> str:
    """The absolute URL of a static file, at its unhashed name.

    Deliberately not ``{% static %}``: in production static file names carry
    a content hash that changes on every deploy, and an email already sitting
    in someone's inbox would lose its images. WhiteNoise keeps serving the
    unhashed original, so this URL stays good.
    """
    return absolute_url(f"{settings.STATIC_URL.rstrip('/')}/{path.lstrip('/')}")


@register.filter
def absolute(url: str | None) -> str:
    """Make a site path absolute; leave a full URL (or nothing) as it is."""
    if not url:
        return ""
    if "://" in url or url.startswith("mailto:"):
        return url
    return absolute_url(url)
