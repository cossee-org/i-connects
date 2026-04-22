from django.shortcuts import redirect, render


def home(request):
    """Landing page for the Django MVP.

    Authenticated users are bounced to their project dashboard; anonymous
    visitors see the marketing/landing page.
    """
    if request.user.is_authenticated:
        return redirect("projects:my_list")
    return render(request, "core/home.html")
