import uuid
from django.db import models


class BaseModel(models.Model):
    uuid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AppAccess(models.Model):
    """
    Model unmanaged khusus untuk mendefinisikan granular system permissions
    tanpa membuat tabel basis data tambahan.
    """
    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            # Penjualan & Kasir
            ('access_pos', 'Akses Kasir POS'),
            ('void_sale', 'Batalkan / Void Transaksi POS'),
            ('reprint_receipt', 'Cetak Ulang Struk Kasir'),
            ('view_sales_reports', 'Akses Laporan Penjualan & Laba Rugi'),

            # Inventaris & Gudang
            ('view_inventory', 'Lihat Master Produk & Stok'),
            ('manage_products', 'Kelola Master Produk & Harga Tier'),
            ('manage_purchases', 'Kelola Kulakan / Pembelian'),
            ('perform_stock_opname', 'Melakukan Stock Opname'),
            ('perform_daily_closing', 'Melakukan Tutup Harian'),
            ('reopen_daily_closing', 'Buka Kembali Tutup Harian'),

            # Anggota & Keuangan
            ('view_members', 'Lihat Data Anggota & Kartu'),
            ('manage_members', 'Kelola Master Anggota & Kartu'),
            ('validate_topup', 'Validasi / Approve Topup Saldo'),
            ('withdraw_deposit', 'Tarik Saldo Deposit Anggota'),
            ('reverse_transactions', 'Reversal Topup / Tarik Deposit'),

            # Manajemen Sistem & Staf
            ('manage_staff', 'Kelola Akun & Penugasan Role Staff'),
            ('manage_role_permissions', 'Atur Matriks Izin Role'),
        ]
