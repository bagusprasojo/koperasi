MENU_PERMISSIONS = {
    'member_topup_request': 'core.access_member_topup_request',
    'member_balance': 'core.access_member_balance',
    'member_ledger': 'core.access_member_ledger',
    'member_purchases': 'core.access_member_purchases',
    'category': 'core.access_category_menu',
    'unit': 'core.access_unit_menu',
    'supplier': 'core.access_supplier_menu',
    'product': 'core.access_product_menu',
    'member_master': 'core.access_member_master_menu',
    'member_card': 'core.access_member_card_menu',
    'pos': 'core.access_pos_menu',
    'purchase': 'core.access_purchase_menu',
    'internal_used': 'core.access_internal_used_menu',
    'stock_opname': 'core.access_stock_opname_menu',
    'admin_topup': 'core.access_admin_topup_menu',
    'withdrawal': 'core.access_withdrawal_menu',
    'daily_closing': 'core.access_daily_closing_menu',
    'sales_daily_summary': 'core.access_sales_daily_summary_report',
    'sales_product_report': 'core.access_sales_product_report',
    'profit_loss_report': 'core.access_profit_loss_report',
    'stock_card_report': 'core.access_stock_card_report',
    'reorder_alert_report': 'core.access_reorder_alert_report',
    'member_ledger_report': 'core.access_member_ledger_report',
    'topup_validation': 'core.access_topup_validation_menu',
}


def menu_permission_context(user):
    if not user.is_authenticated:
        return {f'can_{name}': False for name in MENU_PERMISSIONS}
    return {f'can_{name}': user.has_perm(perm) for name, perm in MENU_PERMISSIONS.items()}
