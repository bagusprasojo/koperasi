import json
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone

from core.constants import Role
from members.models import Member, MemberCard
from sales.models import Sale, SalePayment
from sales.services import get_default_member, search_members

User = get_user_model()


class PosMemberBarcodeScanTests(TestCase):
    def setUp(self):
        self.kasir_group, _ = Group.objects.get_or_create(name=Role.KASIR)
        self.kasir_user = User.objects.create_user(username='kasir_test', password='password123')
        self.kasir_user.groups.add(self.kasir_group)

        # Create members
        self.m1 = Member.objects.create(
            code='MBR-001',
            full_name='Bagus Prasojo',
            phone='0811112222',
            is_active=True,
        )
        self.card1 = MemberCard.objects.create(
            member=self.m1,
            card_number='345678',
            status=MemberCard.STATUS_ACTIVE,
        )

        self.m2 = Member.objects.create(
            code='MBR-002',
            full_name='Budi Santoso',
            phone='081234567890',  # contains '345678'
            is_active=True,
        )
        self.card2 = MemberCard.objects.create(
            member=self.m2,
            card_number='CRD-888',
            status=MemberCard.STATUS_ACTIVE,
        )

        self.m3 = Member.objects.create(
            code='MBR-999',
            full_name='Siti Aminah',
            phone='0855556666',
            is_active=True,
        )

    def test_search_members_prioritizes_exact_card_number(self):
        results = list(search_members('345678'))
        self.assertGreaterEqual(len(results), 2)
        # Member dengan kartu persis '345678' harus berada di urutan pertama
        self.assertEqual(results[0].id, self.m1.id)
        self.assertEqual(results[0].full_name, 'Bagus Prasojo')
        self.assertEqual(results[0].card.card_number, '345678')

    def test_search_members_matches_member_code(self):
        results = list(search_members('MBR-999'))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, self.m3.id)
        self.assertEqual(results[0].code, 'MBR-999')

    def test_pos_member_search_api_exact_card_flag(self):
        self.client.force_login(self.kasir_user)
        response = self.client.get('/sales/pos/api/members?q=345678')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get('success'))
        rows = data.get('data', [])
        self.assertGreaterEqual(len(rows), 2)

        # First row is exact card match
        first = rows[0]
        self.assertEqual(first['id'], self.m1.id)
        self.assertEqual(first['full_name'], 'Bagus Prasojo')
        self.assertEqual(first['card_number'], '345678')
        self.assertTrue(first['is_exact'])

        # Second row is partial phone match
        second = rows[1]
        self.assertEqual(second['id'], self.m2.id)
        self.assertFalse(second['is_exact'])

    def test_pos_member_search_api_by_member_code(self):
        self.client.force_login(self.kasir_user)
        response = self.client.get('/sales/pos/api/members?q=MBR-001')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get('success'))
        rows = data.get('data', [])
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(rows[0]['code'], 'MBR-001')
        self.assertTrue(rows[0]['is_exact'])


class SalesDailySummaryReportTests(TestCase):
    def setUp(self):
        self.admin_group, _ = Group.objects.get_or_create(name=Role.ADMIN_TOKO)
        self.user = User.objects.create_user(username='admin_test', password='password123')
        self.user.groups.add(self.admin_group)

        # Real member
        self.member = Member.objects.create(
            code='MBR-TEST',
            full_name='Anggota Asli',
            phone='08123456789',
            is_active=True,
        )

        # Default walk-in non-member
        self.non_member = get_default_member()

        # 1. Transaction by real member
        self.sale1 = Sale.objects.create(
            sale_number='SL-TEST-001',
            client_txn_id='client-001',
            member=self.member,
            subtotal=Decimal('50000.00'),
            total=Decimal('50000.00'),
            created_by=self.user,
        )
        SalePayment.objects.create(
            sale=self.sale1,
            method=SalePayment.METHOD_CASH,
            amount=Decimal('50000.00'),
            received_amount=Decimal('50000.00'),
        )

        # 2. Transaction by walk-in non-member (points to default member with phone='0000000000')
        self.sale2 = Sale.objects.create(
            sale_number='SL-TEST-002',
            client_txn_id='client-002',
            member=self.non_member,
            subtotal=Decimal('25000.00'),
            total=Decimal('25000.00'),
            created_by=self.user,
        )
        SalePayment.objects.create(
            sale=self.sale2,
            method=SalePayment.METHOD_CASH,
            amount=Decimal('25000.00'),
            received_amount=Decimal('25000.00'),
        )

        # 3. Transaction with member=None (in case any transaction has null member)
        self.sale3 = Sale.objects.create(
            sale_number='SL-TEST-003',
            client_txn_id='client-003',
            member=None,
            subtotal=Decimal('10000.00'),
            total=Decimal('10000.00'),
            created_by=self.user,
        )
        SalePayment.objects.create(
            sale=self.sale3,
            method=SalePayment.METHOD_CASH,
            amount=Decimal('10000.00'),
            received_amount=Decimal('10000.00'),
        )

    def test_daily_summary_correctly_separates_member_and_non_member(self):
        self.client.force_login(self.user)
        today_str = timezone.localdate().isoformat()
        response = self.client.get(f'/sales/reports/daily-summary/?date_from={today_str}&date_to={today_str}')
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        self.assertEqual(len(rows), 1)
        row = rows[0]
        # Total transactions = 3
        self.assertEqual(row['total_transactions'], 3)
        # Real member = 1
        self.assertEqual(row['member_transactions'], 1)
        # Non-member (walk-in + null) = 2
        self.assertEqual(row['non_member_transactions'], 2)

    def test_daily_summary_export_csv_separates_member_and_non_member(self):
        self.client.force_login(self.user)
        today_str = timezone.localdate().isoformat()
        response = self.client.get(f'/sales/reports/daily-summary/export/csv/?date_from={today_str}&date_to={today_str}')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        lines = content.strip().splitlines()
        self.assertEqual(len(lines), 2)  # header + 1 data line
        data_parts = lines[1].split(',')
        self.assertEqual(data_parts[6], '1')  # member_trx
        self.assertEqual(data_parts[7], '2')  # non_member_trx

    def test_daily_summary_detail_pagination(self):
        self.client.force_login(self.user)
        today_str = timezone.localdate().isoformat()

        # Create 22 more transactions so total = 25 (over page limit of 20)
        for i in range(4, 26):
            Sale.objects.create(
                sale_number=f'SL-PAGINATE-{i:03d}',
                client_txn_id=f'client-paginate-{i:03d}',
                member=self.member,
                subtotal=Decimal('10000.00'),
                total=Decimal('10000.00'),
                created_by=self.user,
            )

        # Page 1
        resp_p1 = self.client.get(f'/sales/reports/daily-summary/{today_str}/?page=1')
        self.assertEqual(resp_p1.status_code, 200)
        self.assertIn('page_obj', resp_p1.context)
        page_obj = resp_p1.context['page_obj']
        self.assertEqual(page_obj.paginator.count, 25)
        self.assertEqual(page_obj.paginator.num_pages, 2)
        self.assertEqual(len(resp_p1.context['rows']), 20)
        self.assertContains(resp_p1, 'Berikutnya')
        self.assertNotContains(resp_p1, 'Sebelumnya')

        # Page 2
        resp_p2 = self.client.get(f'/sales/reports/daily-summary/{today_str}/?page=2')
        self.assertEqual(resp_p2.status_code, 200)
        self.assertEqual(len(resp_p2.context['rows']), 5)
        self.assertContains(resp_p2, 'Sebelumnya')
        self.assertNotContains(resp_p2, 'Berikutnya')
