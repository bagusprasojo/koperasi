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
            ('manage_consignments', 'Kelola Barang Titipan / Konsinyasi'),
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
            ('manage_settings', 'Kelola Pengaturan Toko & POS'),
        ]


class StoreSetting(BaseModel):
    store_name = models.CharField(max_length=150, default='Koperasi', verbose_name='Nama Toko / Koperasi')
    store_address = models.CharField(max_length=255, blank=True, default='', verbose_name='Alamat Toko')
    store_phone = models.CharField(max_length=50, blank=True, default='', verbose_name='No. Telepon / WhatsApp')
    receipt_footer = models.CharField(
        max_length=255,
        blank=True,
        default='Terima Kasih Atas Kunjungan Anda',
        verbose_name='Catatan Footer Struk (Catatan Kaki)',
    )
    pos_allow_negative_stock = models.BooleanField(
        default=False,
        verbose_name='Izinkan Transaksi POS Saat Stok Habis / Minus',
        help_text='Jika aktif, kasir dapat menyelesaikan penjualan saat stok sistem 0 atau kurang (stok tercatat minus hingga barang masuk diinput gudang).',
    )

    class Meta:
        verbose_name = 'Pengaturan Toko & POS'
        verbose_name_plural = 'Pengaturan Toko & POS'

    def __str__(self):
        return f"{self.store_name} Settings"

    @classmethod
    def get_settings(cls):
        try:
            setting = cls.objects.first()
            if not setting:
                setting = cls.objects.create(
                    store_name='Koperasi',
                    receipt_footer='Terima Kasih Atas Kunjungan Anda',
                    pos_allow_negative_stock=False,
                )
            return setting
        except Exception:
            class FallbackSetting:
                store_name = 'Koperasi'
                store_address = ''
                store_phone = ''
                receipt_footer = 'Terima Kasih Atas Kunjungan Anda'
                pos_allow_negative_stock = False
            return FallbackSetting()
