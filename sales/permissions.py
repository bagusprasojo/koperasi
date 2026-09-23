from core.constants import Role
from core.rbac import get_user_roles


def can_view_sale_detail(user, sale, request=None) -> bool:
    """
    Memvalidasi apakah user berhak melihat detail transaksi penjualan.
    - Superuser, Admin Toko, dan Kasir berhak melihat seluruh transaksi.
    - Member hanya berhak melihat transaksi miliknya sendiri.
    """
    if not user or not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    roles = get_user_roles(user, request=request)
    if Role.ADMIN_TOKO in roles or Role.KASIR in roles:
        return True

    if Role.MEMBER in roles:
        member_profile = getattr(user, 'member_profile', None)
        return bool(member_profile and sale.member_id == member_profile.id)

    return False
