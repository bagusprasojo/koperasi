from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render


def home(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return redirect('login')


@login_required
def dashboard(request):
    user_roles = list(request.user.groups.values_list('name', flat=True))
    return render(
        request,
        'core/dashboard.html',
        {'user_roles': user_roles},
    )
