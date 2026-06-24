from .access import menu_permission_context


def role_flags(request):
    if not request.user.is_authenticated:
        context = {
            'is_member_role': False,
            'is_admin_toko_role': False,
            'is_kasir_role': False,
            'is_pembelian_role': False,
            'pending_topup_count': 0,
        }
        context.update(menu_permission_context(request.user))
        context.update(
            {
                'has_member_menu': False,
                'has_master_menu': False,
                'has_transaction_menu': False,
                'has_sales_report_menu': False,
                'has_report_menu': False,
                'has_backoffice_menu': False,
            }
        )
        return context
    group_names = set(request.user.groups.values_list('name', flat=True))
    permission_context = menu_permission_context(request.user)
    pending_topup_count = 0
    if permission_context.get('can_topup_validation'):
        from members.models import MemberTopUp
        pending_topup_count = MemberTopUp.objects.filter(status=MemberTopUp.STATUS_PENDING).count()
    context = {
        'is_member_role': 'member' in group_names,
        'is_admin_toko_role': 'admin_toko' in group_names,
        'is_kasir_role': 'kasir' in group_names,
        'is_pembelian_role': 'pembelian' in group_names,
        'pending_topup_count': pending_topup_count,
    }
    context.update(permission_context)
    context.update(
        {
            'has_member_menu': any(
                permission_context.get(key)
                for key in ['can_member_topup_request', 'can_member_balance', 'can_member_ledger', 'can_member_purchases']
            ),
            'has_master_menu': any(
                permission_context.get(key)
                for key in ['can_category', 'can_unit', 'can_supplier', 'can_product', 'can_member_master', 'can_member_card']
            ),
            'has_transaction_menu': any(
                permission_context.get(key)
                for key in [
                    'can_pos',
                    'can_purchase',
                    'can_internal_used',
                    'can_stock_opname',
                    'can_admin_topup',
                    'can_withdrawal',
                    'can_daily_closing',
                ]
            ),
            'has_sales_report_menu': any(
                permission_context.get(key)
                for key in ['can_sales_daily_summary', 'can_sales_product_report', 'can_profit_loss_report']
            ),
            'has_report_menu': any(
                permission_context.get(key)
                for key in [
                    'can_sales_daily_summary',
                    'can_sales_product_report',
                    'can_profit_loss_report',
                    'can_stock_card_report',
                    'can_reorder_alert_report',
                    'can_member_ledger_report',
                ]
            ),
            'has_backoffice_menu': any(
                permission_context.get(key)
                for key in [
                    'can_category',
                    'can_unit',
                    'can_supplier',
                    'can_product',
                    'can_member_master',
                    'can_member_card',
                    'can_pos',
                    'can_purchase',
                    'can_internal_used',
                    'can_stock_opname',
                    'can_admin_topup',
                    'can_withdrawal',
                    'can_daily_closing',
                    'can_sales_daily_summary',
                    'can_sales_product_report',
                    'can_profit_loss_report',
                    'can_stock_card_report',
                    'can_reorder_alert_report',
                    'can_member_ledger_report',
                ]
            ),
        }
    )
    return context
