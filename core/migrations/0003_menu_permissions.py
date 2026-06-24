from django.db import migrations


PERMISSIONS = [
    ('access_member_topup_request', 'Can access member topup request menu'),
    ('access_member_balance', 'Can access member balance menu'),
    ('access_member_ledger', 'Can access member ledger menu'),
    ('access_member_purchases', 'Can access member purchases menu'),
    ('access_category_menu', 'Can access category menu'),
    ('access_unit_menu', 'Can access unit menu'),
    ('access_supplier_menu', 'Can access supplier menu'),
    ('access_product_menu', 'Can access product menu'),
    ('access_member_master_menu', 'Can access member master menu'),
    ('access_member_card_menu', 'Can access member card menu'),
    ('access_pos_menu', 'Can access POS menu'),
    ('access_purchase_menu', 'Can access purchase menu'),
    ('access_internal_used_menu', 'Can access internal used menu'),
    ('access_stock_opname_menu', 'Can access stock opname menu'),
    ('access_admin_topup_menu', 'Can access admin topup menu'),
    ('access_withdrawal_menu', 'Can access withdrawal menu'),
    ('access_daily_closing_menu', 'Can access daily closing menu'),
    ('access_sales_daily_summary_report', 'Can access sales daily summary report'),
    ('access_sales_product_report', 'Can access sales product report'),
    ('access_profit_loss_report', 'Can access profit loss report'),
    ('access_stock_card_report', 'Can access stock card report'),
    ('access_reorder_alert_report', 'Can access reorder alert report'),
    ('access_member_ledger_report', 'Can access member ledger report'),
    ('access_topup_validation_menu', 'Can access topup validation menu'),
]

MEMBER_PERMS = [
    'access_member_topup_request',
    'access_member_balance',
    'access_member_ledger',
    'access_member_purchases',
]

SHARED_BACKOFFICE_PERMS = [
    'access_category_menu',
    'access_unit_menu',
    'access_supplier_menu',
    'access_product_menu',
    'access_member_master_menu',
    'access_member_card_menu',
    'access_pos_menu',
    'access_purchase_menu',
    'access_internal_used_menu',
    'access_stock_opname_menu',
    'access_sales_daily_summary_report',
    'access_sales_product_report',
    'access_profit_loss_report',
    'access_stock_card_report',
    'access_reorder_alert_report',
    'access_member_ledger_report',
]

ADMIN_ONLY_PERMS = [
    'access_admin_topup_menu',
    'access_withdrawal_menu',
    'access_daily_closing_menu',
    'access_topup_validation_menu',
]


def add_menu_permissions(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    Group = apps.get_model('auth', 'Group')

    content_type, _ = ContentType.objects.get_or_create(app_label='core', model='appmenu')
    permission_by_codename = {}
    for codename, name in PERMISSIONS:
        permission, _ = Permission.objects.get_or_create(
            content_type=content_type,
            codename=codename,
            defaults={'name': name},
        )
        permission_by_codename[codename] = permission

    for role_name in ['kasir', 'pembelian']:
        group, _ = Group.objects.get_or_create(name=role_name)
        group.permissions.add(*(permission_by_codename[codename] for codename in SHARED_BACKOFFICE_PERMS))

    admin_group, _ = Group.objects.get_or_create(name='admin_toko')
    admin_group.permissions.add(
        *(permission_by_codename[codename] for codename in SHARED_BACKOFFICE_PERMS + ADMIN_ONLY_PERMS)
    )

    member_group, _ = Group.objects.get_or_create(name='member')
    member_group.permissions.add(*(permission_by_codename[codename] for codename in MEMBER_PERMS))


def remove_menu_permissions(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    content_type = ContentType.objects.filter(app_label='core', model='appmenu').first()
    if content_type:
        Permission.objects.filter(content_type=content_type, codename__in=[p[0] for p in PERMISSIONS]).delete()
        content_type.delete()


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0002_add_member_role'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(add_menu_permissions, remove_menu_permissions),
    ]
