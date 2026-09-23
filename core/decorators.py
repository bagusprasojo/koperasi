from functools import wraps
import logging

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .rbac import has_permission_or_role, get_user_roles

logger = logging.getLogger('security.rbac')


def role_required(*role_names, perm: str = None):
    """
    Memastikan user yang login memiliki salah satu peran yang diizinkan ATAU memiliki permission spesifik.
    - Superuser diizinkan secara otomatis (bypass).
    - Memeriksa permission dinamis grup (jika `perm` diberikan) dengan fallback ke `role_names`.
    - Memanfaatkan caching peran pada request untuk efisiensi query DB.
    - Mencatat warning audit log bila akses ditolak.
    """
    target_roles = tuple(str(r) for r in role_names)

    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if has_permission_or_role(request.user, perm, *target_roles, request=request):
                return view_func(request, *args, **kwargs)

            user_roles = get_user_roles(request.user, request=request)
            ip_addr = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', 'unknown'))
            logger.warning(
                "Akses ditolak (403): User '%s' (roles: %s) mencoba akses '%s' (butuh perm: '%s' / roles: %s). IP: %s",
                request.user.username,
                list(user_roles),
                request.path,
                perm or '-',
                list(target_roles),
                ip_addr,
            )
            raise PermissionDenied("Anda tidak punya akses ke halaman ini.")

        return _wrapped_view

    return decorator
