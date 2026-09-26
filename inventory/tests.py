from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse

from core.constants import Role
from inventory.models import Category, InventoryTransaction, Product, StockLedger, Unit

User = get_user_model()


class StockCardReportTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_group, _ = Group.objects.get_or_create(name=Role.ADMIN_TOKO)
        self.admin_user = User.objects.create_user(username='admin_stock', password='password123')
        self.admin_user.groups.add(self.admin_group)

        self.category = Category.objects.create(name='Sembako')
        self.unit = Unit.objects.create(name='Pcs', code='pcs')
        self.product = Product.objects.create(
            category=self.category,
            name='Beras Rojolele 5kg',
            sku='BRS-ROJO-5',
            barcode='8991234567890',
            unit=self.unit,
            stock=Decimal('25'),
            cost_of_goods_sold=Decimal('50000'),
        )

        # Create transactions and ledgers
        # Tx 1: 2026-09-01, In: 30, Bal: 30
        self.tx1 = InventoryTransaction.objects.create(
            tx_number='TX-20260901-001',
            tx_type=InventoryTransaction.TYPE_PURCHASE,
            tx_date='2026-09-01',
            created_by=self.admin_user,
        )
        self.ledger1 = StockLedger.objects.create(
            product=self.product,
            tx=self.tx1,
            tx_date='2026-09-01',
            qty_in=Decimal('30'),
            qty_out=Decimal('0'),
            balance_before=Decimal('0'),
            balance_after=Decimal('30'),
            unit_cost_at_txn=Decimal('50000'),
            note='Penerimaan PO #001',
        )

        # Tx 2: 2026-09-10, Out: 5, Bal: 25
        self.tx2 = InventoryTransaction.objects.create(
            tx_number='TX-20260910-001',
            tx_type=InventoryTransaction.TYPE_POS_SALE,
            tx_date='2026-09-10',
            created_by=self.admin_user,
        )
        self.ledger2 = StockLedger.objects.create(
            product=self.product,
            tx=self.tx2,
            tx_date='2026-09-10',
            qty_in=Decimal('0'),
            qty_out=Decimal('5'),
            balance_before=Decimal('30'),
            balance_after=Decimal('25'),
            unit_cost_at_txn=Decimal('50000'),
            note='Penjualan POS',
        )

    def test_stock_card_report_all_time(self):
        self.client.force_login(self.admin_user)
        url = reverse('stock_card_report')
        resp = self.client.get(url, {'product_id': str(self.product.id)})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['opening_balance'], Decimal('0'))
        self.assertEqual(resp.context['total_qty_in'], Decimal('30'))
        self.assertEqual(resp.context['total_qty_out'], Decimal('5'))
        self.assertEqual(resp.context['closing_balance'], Decimal('25'))
        self.assertEqual(len(resp.context['ledgers']), 2)

    def test_stock_card_report_with_date_filter_opening_balance(self):
        self.client.force_login(self.admin_user)
        url = reverse('stock_card_report')
        # Filter starting after Tx 1 (from 2026-09-05)
        resp = self.client.get(url, {
            'product_id': str(self.product.id),
            'date_from': '2026-09-05',
            'date_to': '2026-09-20',
        })
        self.assertEqual(resp.status_code, 200)
        # Opening balance before 2026-09-05 should be 30 (from Tx 1)
        self.assertEqual(resp.context['opening_balance'], Decimal('30'))
        self.assertEqual(resp.context['total_qty_in'], Decimal('0'))
        self.assertEqual(resp.context['total_qty_out'], Decimal('5'))
        self.assertEqual(resp.context['closing_balance'], Decimal('25'))
        self.assertEqual(len(resp.context['ledgers']), 1)
        self.assertContains(resp, 'SALDO AWAL')
