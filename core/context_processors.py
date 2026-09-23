from .constants import Role
from .rbac import get_user_roles


def role_flags(request):
    user = request.user
    if not user.is_authenticated:
        return {
            'is_member_role': False,
            'is_admin_toko_role': False,
            'is_kasir_role': False,
            'is_pembelian_role': False,
            'is_staff_any': False,
            'pending_topup_count': 0,
        }

    roles = get_user_roles(user, request=request)
    is_admin = user.is_superuser or (Role.ADMIN_TOKO in roles)

    can_validate_topup = is_admin or user.has_perm('core.validate_topup')
    pending_topup_count = 0
    if can_validate_topup:
        from members.models import MemberTopUp
        pending_topup_count = MemberTopUp.objects.filter(status=MemberTopUp.STATUS_PENDING).count()

    is_kasir = is_admin or (Role.KASIR in roles)
    is_pembelian = is_admin or (Role.PEMBELIAN in roles)
    is_staff = is_admin or bool(roles.intersection({Role.KASIR, Role.PEMBELIAN}))

    return {
        'is_member_role': Role.MEMBER in roles,
        'is_admin_toko_role': is_admin,
        'is_kasir_role': is_kasir,
        'is_pembelian_role': is_pembelian,
        'is_staff_any': is_staff,
        'pending_topup_count': pending_topup_count,
    }
