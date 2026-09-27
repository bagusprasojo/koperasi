import os
import shutil
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Membersihkan data transaksi uji coba/dummy untuk persiapan implementasi production (go-live)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--include-master-data',
            action='store_true',
            dest='include_master_data',
            default=False,
            help='Hapus juga seluruh master data (produk, harga, member, supplier, kategori). Gunakan dengan sangat hati-hati!',
        )
        parser.add_argument(
            '--clean-media',
            action='store_true',
            dest='clean_media',
            default=True,
            help='Hapus file bukti transfer uji coba di folder media/topup_proofs (default: True).',
        )
        parser.add_argument(
            '--yes',
            action='store_true',
            dest='auto_confirm',
            default=False,
            help='Lewati konfirmasi prompt interaktif.',
        )

    def handle(self, *args, **options):
        include_master = options['include_master_data']
        auto_confirm = options['auto_confirm']
        clean_media = options['clean_media']

        self.stdout.write(self.style.WARNING("=" * 70))
        self.stdout.write(self.style.WARNING("PERINGATAN: PEMBERSIHAN DATA UNTUK GO-LIVE PRODUCTION"))
        self.stdout.write(self.style.WARNING("=" * 70))
        if include_master:
            self.stdout.write(self.style.ERROR(
                "Mode: RESET TOTAL (Transaksi + Master Data Produk & Anggota akan DIHAPUS BERSIH)!\n"
                "Akun admin & user auth tetap dipertahankan."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                "Mode: RESET TRANSAKSI SAJA (REKOMENDASI GO-LIVE)\n"
                "- Menghapus seluruh riwayat penjualan, konsinyasi, mutasi stok, tutup buku, & deposit.\n"
                "- Mereset stok produk ke 0 dan saldo anggota ke 0.\n"
                "- MASTER DATA (Produk, Harga, Anggota, Kartu, Supplier, Akun) TETAP AMAN."
            ))
        self.stdout.write(self.style.WARNING("=" * 70))

        if not auto_confirm:
            confirm = input("Ketik 'BERSIHKAN' untuk melanjutkan eksekusi: ").strip()
            if confirm != 'BERSIHKAN':
                self.stdout.write(self.style.ERROR("Operasi dibatalkan. Tidak ada data yang diubah."))
                return

        vendor = connection.vendor
        self.stdout.write(f"\n[1/3] Memulai pembersihan tabel database (Engine: {vendor})...")

        # Daftar tabel transaksi yang selalu dibersihkan
        transaction_tables = [
            'sales_receiptprintjob',
            'sales_salepayment',
            'sales_saleitem',
            'sales_sale',
            'inventory_consignmentbatchitem',
            'inventory_consignmentbatch',
            'inventory_stockledger',
            'inventory_inventorytransactionitem',
            'inventory_inventorytransaction',
            'inventory_productdailysnapshot',
            'inventory_memberdailysnapshot',
            'inventory_dailyclosing',
            'members_memberdepositauditlog',
            'members_memberledger',
            'members_memberwithdrawal',
            'members_membertopup',
            'django_session',
            'django_admin_log',
        ]

        master_tables = [
            'inventory_productpricetier',
            'inventory_product',
            'members_membercard',
            'members_memberwallet',
            'inventory_consignor',
            'members_member',
            'inventory_supplier',
            'inventory_category',
            'inventory_unit',
        ]

        tables_to_truncate = list(transaction_tables)
        if include_master:
            tables_to_truncate.extend(master_tables)

        with connection.cursor() as cursor:
            # Disable FK checks
            if vendor == 'mysql':
                cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
            elif vendor == 'sqlite':
                cursor.execute("PRAGMA foreign_keys = OFF;")

            # Truncate / Delete
            for table in tables_to_truncate:
                try:
                    if vendor == 'mysql':
                        cursor.execute(f"TRUNCATE TABLE `{table}`;")
                    else:
                        cursor.execute(f"DELETE FROM `{table}`;")
                        cursor.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}';")
                    self.stdout.write(f"  - Berhasil membersihkan tabel: {table}")
                except Exception as exc:
                    self.stdout.write(self.style.WARNING(f"  - Skip tabel {table}: {exc}"))

            # Reset Stock & Balance if not deleting master data
            if not include_master:
                try:
                    cursor.execute("UPDATE `inventory_product` SET `stock` = 0.000;")
                    self.stdout.write(self.style.SUCCESS("  - Berhasil mereset stok semua produk ke 0.000"))
                except Exception as exc:
                    self.stdout.write(self.style.WARNING(f"  - Skip reset stok: {exc}"))

                try:
                    cursor.execute("UPDATE `members_memberwallet` SET `balance` = 0.00;")
                    self.stdout.write(self.style.SUCCESS("  - Berhasil mereset saldo dompet anggota ke 0.00"))
                except Exception as exc:
                    self.stdout.write(self.style.WARNING(f"  - Skip reset wallet: {exc}"))

            # Re-enable FK checks
            if vendor == 'mysql':
                cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
            elif vendor == 'sqlite':
                cursor.execute("PRAGMA foreign_keys = ON;")

        # [2/3] Clean dummy media files
        self.stdout.write("\n[2/3] Memeriksa file media bukti top-up dummy...")
        if clean_media:
            topup_proofs_dir = Path(settings.MEDIA_ROOT) / 'topup_proofs'
            if topup_proofs_dir.exists():
                removed_count = 0
                for item in topup_proofs_dir.iterdir():
                    if item.is_file() and item.name != '.gitkeep':
                        try:
                            item.unlink()
                            removed_count += 1
                        except OSError as e:
                            self.stdout.write(self.style.WARNING(f"  - Gagal menghapus {item.name}: {e}"))
                self.stdout.write(self.style.SUCCESS(f"  - Berhasil menghapus {removed_count} file bukti upload di media/topup_proofs/"))
            else:
                self.stdout.write("  - Folder media/topup_proofs tidak ditemukan, dilewati.")

        # [3/3] Verification
        self.stdout.write("\n[3/3] Verifikasi sisa baris data:")
        with connection.cursor() as cursor:
            for table in transaction_tables:
                try:
                    if vendor == 'mysql':
                        cursor.execute(f"SELECT COUNT(*) FROM `{table}`;")
                    else:
                        cursor.execute(f"SELECT COUNT(*) FROM \"{table}\";")
                    cnt = cursor.fetchone()[0]
                    status = self.style.SUCCESS("BERSIH (0)") if cnt == 0 else self.style.ERROR(f"TERISA ({cnt})")
                    self.stdout.write(f"  * {table:<36}: {status}")
                except Exception:
                    pass

        self.stdout.write(self.style.SUCCESS("\nSELESAI! Database siap digunakan untuk transaksi resmi (Go-Live)."))
