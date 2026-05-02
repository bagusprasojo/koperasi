def role_flags(request):
    if not request.user.is_authenticated:
        return {
            'is_member_role': False,
            'is_admin_toko_role': False,
            'is_kasir_role': False,
            'is_pembelian_role': False,
            'pending_topup_count': 0,
        }
    group_names = set(request.user.groups.values_list('name', flat=True))
    pending_topup_count = 0
    if 'admin_toko' in group_names:
        from members.models import MemberTopUp
        pending_topup_count = MemberTopUp.objects.filter(status=MemberTopUp.STATUS_PENDING).count()
    return {
        'is_member_role': 'member' in group_names,
        'is_admin_toko_role': 'admin_toko' in group_names,
        'is_kasir_role': 'kasir' in group_names,
        'is_pembelian_role': 'pembelian' in group_names,
        'pending_topup_count': pending_topup_count,
    }
