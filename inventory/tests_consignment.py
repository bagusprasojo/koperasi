from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase, Client
from django.urls import reverse

from core.constants import Role
from members.models import Member, MemberWallet
from sales.models import Sale, SaleItem
from sales.services import checkout_pos

from .consignment_services import (
    cancel_consignment_batch,
    create_consignment_product,
    create_or_update_consignor,
    generate_consignor_code,
    generate_consignment_product_sku,
    get_consignment_settlement_preview,
    record_consignment_inflow,
    settle_consignment_batch,
)
from .models import (
    Category,
    ConsignmentBatch,
    ConsignmentBatchItem,
    Consignor,
    InventoryTransaction,
    Product,
    ProductPriceTier,
    StockLedger,
    Unit,
)

User = get_user_model()


class ConsignmentLifecycleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='admin_test',
            email='admin@test.com',
            password='password123',
        )
        self.category = Category.objects.create(name='Kue Basah')
        self.unit_pcs = Unit.objects.create(name='Pcs', code='PCS')

        # Buat member penitip
        self.member_consignor = Member.objects.create(
            full_name='Ibu Siti Penitip',
            phone='081234567890',
            address='Jl. Melati No. 5',
            is_active=True,
        )
        MemberWallet.objects.create(member=self.member_consignor, balance=Decimal('10000.00'))

    def test_consignor_code_and_sku_generation(self):
        code1 = generate_consignor_code('Siti Aminah')
        self.assertTrue(code1.startswith('SITI') or code1.startswith('P'))

        consignor = create_or_update_consignor(
            code='P01',
            name='Ibu Siti',
            phone='081234567890',
            user=self.user,
        )
        self.assertEqual(consignor.code, 'P01')

        # Cek auto SKU
        sku1 = generate_consignment_product_sku(consignor)
        self.assertEqual(sku1, 'TP-P01-001')

        # Buat produk pertama
        prod1 = create_consignment_product(
            consignor=consignor,
            name='Lemper Ayam',
            category_id=self.category.id,
            unit_id=self.unit_pcs.id,
            cost_price=Decimal('2000.00'),
            sale_price=Decimal('2500.00'),
            user=self.user,
        )
        self.assertEqual(prod1.sku, 'TP-P01-001')
        self.assertTrue(prod1.is_consignment)
        self.assertEqual(prod1.consignor, consignor)
        self.assertEqual(prod1.stock, Decimal('0.000'))
        self.assertEqual(prod1.cost_of_goods_sold, Decimal('2000.00'))

        # Cek SKU produk kedua
        sku2 = generate_consignment_product_sku(consignor)
        self.assertEqual(sku2, 'TP-P01-002')

    def test_full_daily_consignment_lifecycle(self):
        """
        Uji skenario penuh satu hari titipan:
        1. Pagi: Ibu Siti menitipkan 20 Lemper Ayam (HPP 2000, Jual 2500)
        2. Siang: Kasir menjual 15 Lemper Ayam lewat POS
        3. Sore: Rekap menunjukkan 15 terjual, 5 tersisa di rak
        4. Pelunasan sore: 5 retur fisik ke Ibu Siti, laku 15 pcs
           Hak penitip: 15 * 2000 = Rp 30.000
           Margin koperasi: 15 * (2500 - 2000) = Rp 7.500
           Stok toko kembali menjadi 0.000!
        """
        consignor = create_or_update_consignor(
            code='P02',
            name='Ibu Sri',
            member_id=self.member_consignor.id,
            user=self.user,
        )
        product = create_consignment_product(
            consignor=consignor,
            name='Lemper Ayam Spesial',
            category_id=self.category.id,
            unit_id=self.unit_pcs.id,
            cost_price=Decimal('2000.00'),
            sale_price=Decimal('2500.00'),
            user=self.user,
        )

        # 1. Terima Titipan Pagi
        items_data = [
            {
                'product': product,
                'qty': Decimal('20.000'),
                'cost_price': Decimal('2000.00'),
                'sale_price': Decimal('2500.00'),
            }
        ]
        batch = record_consignment_inflow(
            consignor=consignor,
            batch_date=date.today(),
            items_data=items_data,
            user=self.user,
            notes='Titipan pagi 20 pcs',
        )

        self.assertEqual(batch.status, ConsignmentBatch.STATUS_OPEN)
        self.assertEqual(batch.total_received_amount, Decimal('40000.00'))

        # Verifikasi stok produk bertambah menjadi 20
        product.refresh_from_db()
        self.assertEqual(product.stock, Decimal('20.000'))

        # Verifikasi transaksi dan ledger tercatat
        self.assertTrue(InventoryTransaction.objects.filter(tx_type=InventoryTransaction.TYPE_CONSIGNMENT_IN).exists())
        self.assertTrue(StockLedger.objects.filter(product=product, qty_in=Decimal('20.000')).exists())

        # 2. Penjualan di POS (15 pcs terjual)
        client_txn_id = f"POS-{uuid4().hex[:12]}"
        sale, is_dup = checkout_pos(
            member_id=None,
            items=[{'product_id': str(product.id), 'qty': '15'}],
            payments=[{'method': 'cash', 'amount': '37500.00'}],
            client_txn_id=client_txn_id,
            user=self.user,
            cash_received_raw='40000',
        )
        self.assertEqual(sale.total, Decimal('37500.00'))

        # Stok sistem sekarang berkurang menjadi 5
        product.refresh_from_db()
        self.assertEqual(product.stock, Decimal('5.000'))

        # 3. Rekapitulasi Sore (Preview)
        preview = get_consignment_settlement_preview(batch)
        item_preview = preview['items'][0]
        self.assertEqual(item_preview['qty_received'], Decimal('20.000'))
        self.assertEqual(item_preview['pos_sold'], Decimal('15.000'))
        self.assertEqual(item_preview['current_stock'], Decimal('5.000'))
        self.assertEqual(item_preview['suggested_returned'], Decimal('5.000'))
        self.assertEqual(item_preview['suggested_sold'], Decimal('15.000'))
        self.assertEqual(item_preview['payable_amount'], Decimal('30000.00'))
        self.assertEqual(item_preview['coop_margin'], Decimal('7500.00'))

        # 4. Eksekusi Pelunasan Sore (Settle via Member Deposit)
        wallet_before = self.member_consignor.wallet.balance
        batch_item = batch.items.first()
        items_settlement = [
            {
                'item_id': str(batch_item.id),
                'qty_returned': '5.000',
                'qty_loss': '0.000',
            }
        ]

        settled_batch = settle_consignment_batch(
            batch=batch,
            items_settlement=items_settlement,
            payout_method=ConsignmentBatch.PAYOUT_METHOD_MEMBER_DEPOSIT,
            user=self.user,
            payout_reference='Ref Pelunasan Titipan Sore',
            notes='Lunas ke dompet saldo anggota',
        )

        self.assertEqual(settled_batch.status, ConsignmentBatch.STATUS_SETTLED)
        self.assertEqual(settled_batch.total_sold_cost, Decimal('30000.00'))
        self.assertEqual(settled_batch.total_sold_retail, Decimal('37500.00'))
        self.assertEqual(settled_batch.total_coop_margin, Decimal('7500.00'))

        # Verifikasi saldo dompet anggota bertambah Rp 30.000
        self.member_consignor.wallet.refresh_from_db()
        self.assertEqual(self.member_consignor.wallet.balance, wallet_before + Decimal('30000.00'))

        # Verifikasi sisa stok 5 di-return sehingga stok akhir produk toko menjadi 0.000!
        product.refresh_from_db()
        self.assertEqual(product.stock, Decimal('0.000'))

        # Verifikasi transaksi consignment_return dibuat
        self.assertTrue(
            InventoryTransaction.objects.filter(tx_type=InventoryTransaction.TYPE_CONSIGNMENT_RETURN).exists()
        )

    def test_consignment_batch_cancellation(self):
        """
        Uji pembatalan batch yang salah input pagi hari:
        Stok yang bertambah harus di-reversal kembali ke 0.
        """
        consignor = create_or_update_consignor(code='P03', name='Ibu Ani', user=self.user)
        product = create_consignment_product(
            consignor=consignor,
            name='Kue Lumpur',
            category_id=self.category.id,
            unit_id=self.unit_pcs.id,
            cost_price=Decimal('1500.00'),
            sale_price=Decimal('2000.00'),
            user=self.user,
        )

        batch = record_consignment_inflow(
            consignor=consignor,
            batch_date=date.today(),
            items_data=[{'product': product, 'qty': Decimal('10.000'), 'cost_price': Decimal('1500.00'), 'sale_price': Decimal('2000.00')}],
            user=self.user,
        )
        product.refresh_from_db()
        self.assertEqual(product.stock, Decimal('10.000'))

        # Batalkan batch
        cancel_consignment_batch(batch, user=self.user, reason='Salah input jumlah kue')
        batch.refresh_from_db()
        self.assertEqual(batch.status, ConsignmentBatch.STATUS_CANCELLED)

        # Stok kembali menjadi 0
        product.refresh_from_db()
        self.assertEqual(product.stock, Decimal('0.000'))

    def test_consignor_views_access(self):
        client = Client()
        client.force_login(self.user)

        # 1. Halaman daftar penitip
        res = client.get(reverse('consignor_list'))
        self.assertEqual(res.status_code, 200)

        # 2. Halaman terima titipan pagi
        res = client.get(reverse('consignment_inflow'))
        self.assertEqual(res.status_code, 200)

        # 3. Halaman daftar rekap sore
        res = client.get(reverse('consignment_settlement_list'))
        self.assertEqual(res.status_code, 200)

        # 4. Halaman laporan titipan
        res = client.get(reverse('consignment_report'))
        self.assertEqual(res.status_code, 200)
