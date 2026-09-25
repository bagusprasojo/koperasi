from django.db import migrations


def grant_manage_consignments(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    ct, _ = ContentType.objects.get_or_create(app_label='core', model='appaccess')
    perm, _ = Permission.objects.get_or_create(
        codename='manage_consignments',
        content_type=ct,
        defaults={'name': 'Kelola Barang Titipan / Konsinyasi'}
    )

    for role_name in ['admin_toko', 'kasir', 'pembelian']:
        group = Group.objects.filter(name=role_name).first()
        if group:
            group.permissions.add(perm)


def revert_grant(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_alter_appaccess_options'),
    ]

    operations = [
        migrations.RunPython(grant_manage_consignments, revert_grant),
    ]
