from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from core.constants import Role, STAFF_ROLES
from core.decorators import role_required

User = get_user_model()

ASSIGNABLE_STAFF_ROLES = [
    (Role.ADMIN_TOKO.value, 'Admin Toko (Akses Penuh Toko)'),
    (Role.KASIR.value, 'Kasir (Operasional POS & Cetak Struk)'),
    (Role.PEMBELIAN.value, 'Bagian Pembelian (Kulakan & Stok Produk)'),
]


def _get_staff_queryset():
    # Mengambil seluruh user staf toko (memiliki grup staf atau superuser),
    # mengecualikan user yang murni hanya bertindak sebagai 'member' pelanggan.
    return (
        User.objects.prefetch_related('groups')
        .filter(
            Q(groups__name__in=STAFF_ROLES) | Q(is_superuser=True) | Q(is_staff=True)
        )
        .distinct()
        .order_by('-is_active', 'username')
    )


@role_required(Role.ADMIN_TOKO, perm='manage_staff')
def staff_list(request):
    query = request.GET.get('q', '').strip()
    role_filter = request.GET.get('role', '').strip()

    qs = _get_staff_queryset()
    if query:
        qs = qs.filter(
            Q(username__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(email__icontains=query)
        )
    if role_filter:
        qs = qs.filter(groups__name=role_filter)

    page_obj = Paginator(qs, 15).get_page(request.GET.get('page'))
    return render(
        request,
        'core/staff_list.html',
        {
            'page_obj': page_obj,
            'query': query,
            'role_filter': role_filter,
            'available_roles': ASSIGNABLE_STAFF_ROLES,
        },
    )


@role_required(Role.ADMIN_TOKO, perm='manage_staff')
def staff_create(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        password_confirm = request.POST.get('password_confirm', '')
        selected_roles = request.POST.getlist('roles')
        is_active = request.POST.get('is_active') == 'on'

        if not username or not password:
            messages.error(request, 'Username dan password wajib diisi.')
        elif password != password_confirm:
            messages.error(request, 'Konfirmasi password tidak cocok.')
        elif User.objects.filter(username__iexact=username).exists():
            messages.error(request, f"Username '{username}' sudah digunakan.")
        elif not selected_roles:
            messages.error(request, 'Pilih minimal 1 peran (role) untuk staf.')
        else:
            try:
                validate_password(password)
                with transaction.atomic():
                    user = User.objects.create_user(
                        username=username,
                        password=password,
                        first_name=first_name,
                        last_name=last_name,
                        email=email,
                        is_active=is_active,
                        is_staff=Role.ADMIN_TOKO in selected_roles,
                    )
                    groups = Group.objects.filter(name__in=selected_roles)
                    user.groups.set(groups)

                messages.success(request, f"Staf '{user.username}' berhasil dibuat.")
                return redirect('staff_list')
            except ValidationError as exc:
                messages.error(request, exc.messages[0] if exc.messages else str(exc))
            except Exception as exc:
                messages.error(request, f'Terjadi kesalahan: {str(exc)}')

    return render(
        request,
        'core/staff_create.html',
        {
            'available_roles': ASSIGNABLE_STAFF_ROLES,
        },
    )


@role_required(Role.ADMIN_TOKO, perm='manage_staff')
def staff_edit(request, user_id):
    target_user = get_object_or_404(User.objects.prefetch_related('groups'), id=user_id)
    current_user_roles = set(target_user.groups.values_list('name', flat=True))
    is_self = target_user.id == request.user.id

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        selected_roles = request.POST.getlist('roles')
        is_active = request.POST.get('is_active') == 'on'

        # Proteksi self-lockout
        if is_self and not is_active:
            messages.error(request, 'Anda tidak dapat menonaktifkan akun Anda sendiri.')
            return redirect('staff_edit', user_id=target_user.id)

        if is_self and Role.ADMIN_TOKO not in selected_roles:
            messages.error(request, 'Anda tidak dapat mencabut hak Admin Toko dari akun Anda sendiri.')
            return redirect('staff_edit', user_id=target_user.id)

        if not selected_roles:
            messages.error(request, 'Pilih minimal 1 peran (role) untuk staf.')
        else:
            try:
                with transaction.atomic():
                    target_user.first_name = first_name
                    target_user.last_name = last_name
                    target_user.email = email
                    target_user.is_active = is_active
                    if Role.ADMIN_TOKO in selected_roles:
                        target_user.is_staff = True
                    target_user.save()

                    # Update grup
                    groups = Group.objects.filter(name__in=selected_roles)
                    # Jika user ini kebetulan punya role member, jangan hapus grup member miliknya
                    if Role.MEMBER in current_user_roles:
                        member_group = Group.objects.filter(name=Role.MEMBER).first()
                        if member_group:
                            groups = list(groups) + [member_group]
                    target_user.groups.set(groups)

                messages.success(request, f"Data staf '{target_user.username}' berhasil diperbarui.")
                return redirect('staff_list')
            except Exception as exc:
                messages.error(request, f'Gagal memperbarui staf: {str(exc)}')

    return render(
        request,
        'core/staff_edit.html',
        {
            'target_user': target_user,
            'current_user_roles': current_user_roles,
            'available_roles': ASSIGNABLE_STAFF_ROLES,
            'is_self': is_self,
        },
    )


@role_required(Role.ADMIN_TOKO, perm='manage_staff')
def staff_password_reset(request, user_id):
    target_user = get_object_or_404(User, id=user_id)
    if request.method == 'POST':
        new_password = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')

        if not new_password:
            messages.error(request, 'Password baru wajib diisi.')
        elif new_password != confirm_password:
            messages.error(request, 'Konfirmasi password baru tidak cocok.')
        else:
            try:
                validate_password(new_password, user=target_user)
                target_user.set_password(new_password)
                target_user.save()
                messages.success(request, f"Password staf '{target_user.username}' berhasil direset.")
                return redirect('staff_list')
            except ValidationError as exc:
                messages.error(request, exc.messages[0] if exc.messages else str(exc))
            except Exception as exc:
                messages.error(request, f'Gagal mereset password: {str(exc)}')

    return render(
        request,
        'core/staff_password.html',
        {
            'target_user': target_user,
        },
    )


@role_required(Role.ADMIN_TOKO, perm='manage_staff')
def staff_toggle_active(request, user_id):
    if request.method == 'POST':
        target_user = get_object_or_404(User, id=user_id)
        if target_user.id == request.user.id:
            messages.error(request, 'Anda tidak dapat menonaktifkan akun Anda sendiri.')
            return redirect('staff_list')

        target_user.is_active = not target_user.is_active
        target_user.save(update_fields=['is_active'])
        status_label = 'diaktifkan' if target_user.is_active else 'dinonaktifkan'
        messages.success(request, f"Akun staf '{target_user.username}' berhasil {status_label}.")

    return redirect('staff_list')
