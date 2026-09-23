from django.db import migrations


def assign_default_permissions(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')

    # Ambil seluruh permission yang terdaftar di app 'core'
    core_perms = {p.codename: p for p in Permission.objects.filter(content_type__app_label='core')}
    if not core_perms:
        return

    # 1. Admin Toko: Memiliki seluruh permission
    admin_group, _ = Group.objects.get_or_create(name='admin_toko')
    admin_group.permissions.set(list(core_perms.values()))

    # 2. Kasir: Akses POS, cetak ulang struk, lihat katalog & member, lihat laporan
    kasir_group, _ = Group.objects.get_or_create(name='kasir')
    kasir_codenames = [
        'access_pos',
        'reprint_receipt',
        'view_inventory',
        'view_members',
        'view_sales_reports',
    ]
    kasir_perms = [core_perms[code] for code in kasir_codenames if code in core_perms]
    kasir_group.permissions.set(kasir_perms)

    # 3. Pembelian: Kelola inventaris, produk, kulakan, opname, dan master member
    pembelian_group, _ = Group.objects.get_or_create(name='pembelian')
    pembelian_codenames = [
        'view_inventory',
        'manage_products',
        'manage_purchases',
        'perform_stock_opname',
        'view_members',
        'manage_members',
        'view_sales_reports',
    ]
    pembelian_perms = [core_perms[code] for code in pembelian_codenames if code in core_perms]
    pembelian_group.permissions.set(pembelian_perms)


def remove_default_permissions(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    for role_name in ['admin_toko', 'kasir', 'pembelian']:
        group = Group.objects.filter(name=role_name).first()
        if group:
            group.permissions.filter(content_type__app_label='core').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_initial'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(assign_default_permissions, remove_default_permissions),
    ]
