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


def app_permission_required(permission_name):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if request.user.has_perm(permission_name):
                return view_func(request, *args, **kwargs)
            raise PermissionDenied("Akun Anda tidak memiliki permission untuk halaman ini.")

        return _wrapped_view

    return decorator


def app_permission_and_role_required(permission_name, *role_names):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.has_perm(permission_name):
                raise PermissionDenied("Akun Anda tidak memiliki permission untuk halaman ini.")
            if not request.user.groups.filter(name__in=role_names).exists():
                raise PermissionDenied("Akun Anda tidak memiliki role yang sesuai untuk halaman ini.")
            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator
