from django.contrib import messages
from django.contrib.auth.models import Group, Permission
from django.db import transaction
from django.shortcuts import render, redirect

from core.constants import Role
from core.decorators import role_required

PERMISSION_MODULES = [
    {
        'title': 'Penjualan & Kasir (POS)',
        'description': 'Akses aplikasi kasir, pembatalan, dan pencetakan struk',
        'permissions': [
            ('access_pos', 'Buka Aplikasi Kasir (POS)', 'Memungkinkan staf membuka antarmuka kasir dan memproses belanja'),
            ('reprint_receipt', 'Cetak Ulang Struk (Reprint)', 'Memungkinkan staf mencetak ulang struk transaksi lama'),
            ('void_sale', 'Batalkan Transaksi (Void)', 'Wewenang supervisor untuk membatalkan transaksi yang salah'),
            ('view_sales_reports', 'Lihat Laporan Penjualan & L/R', 'Melihat rekap penjualan harian, top produk, dan laba rugi'),
        ],
    },
    {
        'title': 'Inventaris & Gudang',
        'description': 'Master produk, harga tier, kulakan, opname, dan tutup harian',
        'permissions': [
            ('view_inventory', 'Lihat Master Produk & Stok', 'Melihat katalog produk, satuan, kategori, dan kartu stok'),
            ('manage_products', 'Kelola Produk & Harga Tier', 'Menambah/mengedit produk, supplier, dan aturan harga bertingkat'),
            ('manage_purchases', 'Kelola Kulakan / Pembelian', 'Membuat, mengubah, dan menghapus transaksi pembelian/supplier'),
            ('perform_stock_opname', 'Melakukan Stock Opname', 'Menyesuaikan fisik stok riil dengan saldo sistem'),
            ('perform_daily_closing', 'Melakukan Tutup Harian', 'Menutup buku kas & stok harian toko (EOD closing)'),
            ('reopen_daily_closing', 'Buka Kembali Tutup Harian', 'Wewenang membuka kembali closing yang terkunci'),
        ],
    },
    {
        'title': 'Anggota & Dompet Saldo',
        'description': 'Master anggota, kartu fisik, topup, penarikan, dan audit',
        'permissions': [
            ('view_members', 'Lihat Data Anggota & Kartu', 'Melihat profil anggota dan nomor kartu terdaftar'),
            ('manage_members', 'Kelola Anggota & Kartu', 'Menambah atau mengedit identitas anggota dan menerbitkan kartu'),
            ('validate_topup', 'Validasi / Approve Topup', 'Menyetujui atau menolak bukti transfer topup saldo'),
            ('withdraw_deposit', 'Tarik Saldo Deposit', 'Memproses penarikan saldo tunai anggota'),
            ('reverse_transactions', 'Reversal Topup / Tarik Tunai', 'Membatalkan mutasi saldo jika terjadi kesalahan input'),
        ],
    },
    {
        'title': 'Manajemen Sistem & Otorisasi',
        'description': 'Pengaturan akun staf dan hak akses dinamis',
        'permissions': [
            ('manage_staff', 'Kelola Akun & Role Staf', 'Membuat user staf, ubah password, dan pilih role staf'),
            ('manage_role_permissions', 'Atur Matriks Izin Role', 'Mengubah checklist izin yang dimiliki masing-masing role'),
        ],
    },
]


@role_required(Role.ADMIN_TOKO, perm='manage_role_permissions')
def role_permission_matrix(request):
    roles = [
        {'name': Role.KASIR.value, 'label': 'Kasir'},
        {'name': Role.PEMBELIAN.value, 'label': 'Pembelian'},
        {'name': Role.ADMIN_TOKO.value, 'label': 'Admin Toko'},
    ]

    groups = {r['name']: Group.objects.get_or_create(name=r['name'])[0] for r in roles}
    core_perms = {p.codename: p for p in Permission.objects.filter(content_type__app_label='core')}

    if request.method == 'POST':
        try:
            with transaction.atomic():
                for role_info in roles:
                    role_name = role_info['name']
                    group = groups[role_name]

                    # Kumpulkan seluruh codename yang dicentang untuk role ini
                    selected_perms = []
                    for module in PERMISSION_MODULES:
                        for codename, _, _ in module['permissions']:
                            field_name = f"perm_{role_name}_{codename}"
                            if request.POST.get(field_name) == 'on':
                                perm_obj = core_perms.get(codename)
                                if perm_obj:
                                    selected_perms.append(perm_obj)

                    # Update ke tabel auth_group_permissions
                    # Simpan permission non-core jika ada agar tidak terhapus
                    non_core_perms = list(group.permissions.exclude(content_type__app_label='core'))
                    group.permissions.set(selected_perms + non_core_perms)

            messages.success(request, 'Matriks hak akses role berhasil disimpan dan langsung aktif!')
            return redirect('role_permission_matrix')
        except Exception as exc:
            messages.error(request, f'Gagal menyimpan matriks hak akses: {str(exc)}')

    # Siapkan state matriks untuk template
    # mapping: { (role_name, codename): boolean }
    group_perms_map = {}
    for role_name, group in groups.items():
        assigned_codenames = set(group.permissions.filter(content_type__app_label='core').values_list('codename', flat=True))
        for module in PERMISSION_MODULES:
            for codename, _, _ in module['permissions']:
                group_perms_map[(role_name, codename)] = codename in assigned_codenames

    return render(
        request,
        'core/role_matrix.html',
        {
            'roles': roles,
            'modules': PERMISSION_MODULES,
            'matrix': group_perms_map,
        },
    )
