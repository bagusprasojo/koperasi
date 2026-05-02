from django.db import migrations


def add_member_role(apps, schema_editor):
    group_model = apps.get_model('auth', 'Group')
    group_model.objects.get_or_create(name='member')


def remove_member_role(apps, schema_editor):
    group_model = apps.get_model('auth', 'Group')
    group_model.objects.filter(name='member').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0001_create_default_roles'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(add_member_role, remove_member_role),
    ]
