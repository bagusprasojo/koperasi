from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def role_required(*role_names):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if request.user.groups.filter(name__in=role_names).exists():
                return view_func(request, *args, **kwargs)
            raise PermissionDenied("Anda tidak punya akses ke halaman ini.")

        return _wrapped_view

    return decorator
