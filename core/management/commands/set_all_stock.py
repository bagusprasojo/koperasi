from datetime import date
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model
from inventory.models import Product, InventoryTransaction, InventoryTransactionItem, StockLedger
from inventory.services import _tx_number


class Command(BaseCommand):
    help = "Mengubah stok semua produk menjadi jumlah tertentu (misal 1000) sekaligus mencatat riwayat ke Kartu Stok (Stock Ledger)."

    def add_arguments(self, parser):
        parser.add_argument(
            'qty',
            nargs='?',
            type=float,
            default=1000.0,
            help='Jumlah stok yang ingin diset untuk semua produk (default: 1000)',
        )
        parser.add_argument(
            '--no-ledger',
            action='store_true',
            dest='no_ledger',
            default=False,
            help='Update langsung field stock tanpa membuat catatan mutasi transaksi / Stock Ledger.',
        )
        parser.add_argument(
            '--yes',
            action='store_true',
            dest='auto_confirm',
            default=False,
            help='Lewati prompt konfirmasi.',
        )

    def handle(self, *args, **options):
        target_qty = Decimal(str(options['qty']))
        no_ledger = options['no_ledger']
        auto_confirm = options['auto_confirm']

        if target_qty < Decimal('0'):
            self.stdout.write(self.style.ERROR("Jumlah stok tidak boleh bernilai negatif!"))
            return

        total_products = Product.objects.count()
        if total_products == 0:
            self.stdout.write(self.style.WARNING("Tidak ada produk yang terdaftar di database."))
            return

        self.stdout.write(self.style.WARNING("=" * 65))
        self.stdout.write(f"UPDATE STOK PRODUK: {total_products} produk -> {target_qty:f} unit")
        self.stdout.write(f"Mode: {'Update Langsung (Tanpa Ledger)' if no_ledger else 'Rapi dengan Riwayat Kartu Stok (Stock Ledger)'}")
        self.stdout.write(self.style.WARNING("=" * 65))

        if not auto_confirm:
            confirm = input(f"Ketik 'YA' untuk mengupdate stok semua ({total_products}) produk menjadi {target_qty}: ").strip()
            if confirm.upper() != 'YA':
                self.stdout.write(self.style.ERROR("Operasi dibatalkan."))
                return

        if no_ledger:
            updated = Product.objects.all().update(stock=target_qty)
            self.stdout.write(self.style.SUCCESS(f"\nBerhasil mengupdate stok {updated} produk menjadi {target_qty} (Direct Update)."))
            return

        User = get_user_model()
        first_admin = User.objects.filter(is_superuser=True).first() or User.objects.first()

        with transaction.atomic():
            tx = InventoryTransaction.objects.create(
                tx_number=_tx_number('SOP'),
                tx_type=InventoryTransaction.TYPE_STOCK_OPNAME,
                tx_date=date.today(),
                note=f'Penetapan Stok Awal Masal {target_qty} unit',
                created_by=first_admin,
            )

            updated_count = 0
            items_to_create = []
            ledgers_to_create = []
            products_to_update = []

            for p in Product.objects.all():
                diff = target_qty - p.stock
                if diff == 0:
                    continue

                before = p.stock
                p.stock = target_qty
                products_to_update.append(p)
                updated_count += 1

                unit_cost = p.cost_of_goods_sold or p.last_purchase_price or Decimal('0')
                total_cost = (unit_cost * abs(diff)).quantize(Decimal('0.01'))

                items_to_create.append(
                    InventoryTransactionItem(
                        transaction=tx,
                        product=p,
                        qty=diff,
                        unit_cost=unit_cost,
                        total_cost=total_cost,
                    )
                )

                ledgers_to_create.append(
                    StockLedger(
                        product=p,
                        tx=tx,
                        tx_date=tx.tx_date,
                        qty_in=max(diff, Decimal('0')),
                        qty_out=max(-diff, Decimal('0')),
                        balance_before=before,
                        balance_after=target_qty,
                        unit_cost_at_txn=unit_cost,
                        value_in=total_cost if diff > 0 else Decimal('0'),
                        value_out=total_cost if diff < 0 else Decimal('0'),
                        note=f'Penetapan Stok Masal ({target_qty})',
                    )
                )

            if products_to_update:
                Product.objects.bulk_update(products_to_update, ['stock', 'updated_at'])
                InventoryTransactionItem.objects.bulk_create(items_to_create)
                StockLedger.objects.bulk_create(ledgers_to_create)

        self.stdout.write(self.style.SUCCESS(
            f"\nSUKSES: Stok {updated_count} produk telah diperbarui menjadi {target_qty}.\n"
            f"Nomor Transaksi: {tx.tx_number}\n"
            f"Mutasi kartu stok telah tercatat rapi sehingga Laporan Kartu Stok sinkron!"
        ))
