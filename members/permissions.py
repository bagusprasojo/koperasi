from core.constants import Role
from core.rbac import get_user_roles


def can_access_topup_proof(user, topup, request=None) -> bool:
    """
    Memvalidasi apakah user berhak mengakses/mengunduh file bukti transfer topup.
    - Superuser & Admin Toko berhak melihat semua bukti transfer.
    - Member hanya berhak melihat bukti transfer miliknya sendiri.
    """
    if not user or not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    roles = get_user_roles(user, request=request)
    if Role.ADMIN_TOKO in roles:
        return True

    member_profile = getattr(user, 'member_profile', None)
    if member_profile and topup.member_id == member_profile.id:
        return True

    return False


def can_access_member_profile(user, target_member, request=None) -> bool:
    """
    Memvalidasi apakah user berhak melihat detail member tertentu.
    - Superuser, Admin Toko, Kasir, Pembelian berhak melihat seluruh data member.
    - Member hanya berhak melihat profilnya sendiri.
    """
    if not user or not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    roles = get_user_roles(user, request=request)
    if roles.intersection({Role.ADMIN_TOKO, Role.KASIR, Role.PEMBELIAN}):
        return True

    member_profile = getattr(user, 'member_profile', None)
    return bool(member_profile and member_profile.id == target_member.id)
