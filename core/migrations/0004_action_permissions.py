from django.db import migrations


ACTION_PERMISSIONS = [
    ('use_pos', 'Can use POS'),
    ('view_product', 'Can view product'),
    ('manage_product', 'Can manage product'),
    ('manage_category', 'Can manage category'),
    ('manage_unit', 'Can manage unit'),
    ('manage_supplier', 'Can manage supplier'),
    ('manage_purchase', 'Can manage purchase'),
    ('manage_internal_used', 'Can manage internal used'),
    ('manage_stock_opname', 'Can manage stock opname'),
    ('manage_daily_closing', 'Can manage daily closing'),
    ('view_member_master', 'Can view member master'),
    ('manage_member_master', 'Can manage member master'),
    ('delete_member_master', 'Can delete member master'),
    ('view_member_card', 'Can view member card'),
    ('manage_member_card', 'Can manage member card'),
    ('delete_member_card', 'Can delete member card'),
    ('admin_topup', 'Can process admin topup'),
    ('validate_topup', 'Can validate topup'),
    ('withdraw_deposit', 'Can withdraw member deposit'),
    ('member_topup_request', 'Can request member topup'),
    ('member_view_balance', 'Can view own member balance'),
    ('member_view_ledger', 'Can view own member ledger'),
    ('member_view_purchases', 'Can view own member purchases'),
]


ADMIN_PERMS = [codename for codename, _ in ACTION_PERMISSIONS]

KASIR_PERMS = [
    'use_pos',
    'view_product',
    'view_member_master',
    'view_member_card',
]

PEMBELIAN_PERMS = [
    'view_product',
    'manage_product',
    'manage_category',
    'manage_unit',
    'manage_supplier',
    'manage_purchase',
    'manage_internal_used',
    'manage_stock_opname',
    'view_member_master',
    'manage_member_master',
    'view_member_card',
    'manage_member_card',
]

MEMBER_PERMS = [
    'member_topup_request',
    'member_view_balance',
    'member_view_ledger',
    'member_view_purchases',
]


def add_action_permissions(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    Group = apps.get_model('auth', 'Group')
    content_type, _ = ContentType.objects.get_or_create(app_label='core', model='appaction')

    permission_by_codename = {}
    for codename, name in ACTION_PERMISSIONS:
        permission, _ = Permission.objects.get_or_create(
            content_type=content_type,
            codename=codename,
            defaults={'name': name},
        )
        permission_by_codename[codename] = permission

    role_map = {
        'admin_toko': ADMIN_PERMS,
        'kasir': KASIR_PERMS,
        'pembelian': PEMBELIAN_PERMS,
        'member': MEMBER_PERMS,
    }
    for role_name, codenames in role_map.items():
        group, _ = Group.objects.get_or_create(name=role_name)
        group.permissions.add(*(permission_by_codename[codename] for codename in codenames))


def remove_action_permissions(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    content_type = ContentType.objects.filter(app_label='core', model='appaction').first()
    if content_type:
        Permission.objects.filter(content_type=content_type, codename__in=[p[0] for p in ACTION_PERMISSIONS]).delete()
        content_type.delete()


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0003_menu_permissions'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(add_action_permissions, remove_action_permissions),
    ]
