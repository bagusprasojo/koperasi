from typing import Iterable, Set


def get_user_roles(user, request=None) -> Set[str]:
    """
    Mengambil set nama peran/grup user dengan request-level caching
    untuk mencegah query SQL berulang ke auth_user_groups.
    Memvalidasi ID user agar aman bila request.user berganti (misal saat testing).
    """
    if not user or not user.is_authenticated:
        return set()

    user_id = getattr(user, 'id', None)

    # Cek cache di request jika request objek disediakan dan user_id cocok
    if (
        request is not None
        and getattr(request, '_cached_user_id', None) == user_id
        and hasattr(request, '_cached_user_roles')
    ):
        return request._cached_user_roles

    # Cek cache di objek user jika ada
    if hasattr(user, '_cached_user_roles'):
        roles = user._cached_user_roles
    else:
        roles = set(user.groups.values_list('name', flat=True))
        user._cached_user_roles = roles

    if request is not None:
        request._cached_user_id = user_id
        request._cached_user_roles = roles

    return roles


def has_any_role(user, *role_names: Iterable[str], request=None) -> bool:
    """
    Memeriksa apakah user memiliki salah satu dari peran yang ditentukan.
    Superuser selalu dianggap memiliki hak (mengembalikan True).
    """
    if not user or not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    target_roles = {str(r) for r in role_names}
    user_roles = get_user_roles(user, request=request)
    return bool(user_roles.intersection(target_roles))


def has_permission_or_role(user, perm_code: str = None, *fallback_roles, request=None) -> bool:
    """
    Evaluasi otorisasi bertingkat:
    1. Superuser selalu lolos.
    2. Jika perm_code diberikan, periksa permission dinamis grup (user.has_perm).
    3. Jika permission belum dimiliki, fallback ke pemeriksaan role statis bawaan.
    """
    if not user or not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    # 1. Cek permission dinamis grup via Django auth permissions
    if perm_code:
        full_perm = perm_code if '.' in perm_code else f'core.{perm_code}'
        if user.has_perm(full_perm):
            return True

    # 2. Fallback ke role-based checking
    if fallback_roles:
        return has_any_role(user, *fallback_roles, request=request)

    return False
