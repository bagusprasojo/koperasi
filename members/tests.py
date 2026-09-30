from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase

from .models import Member, MemberCard, MemberDepositAuditLog, MemberLedger
from .services import (
    approve_topup,
    charge_member_by_card,
    create_admin_topup,
    create_admin_withdrawal,
    get_or_create_wallet,
    request_member_topup,
    reverse_topup,
    reverse_withdrawal,
)


User = get_user_model()


class MemberDepositServiceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin', password='admin-pass')
        self.member_user = User.objects.create_user(username='MBR001', password='member-pass')
        self.member = Member.objects.create(
            code='MBR001',
            user=self.member_user,
            full_name='Member Test',
            phone='081234567890',
            email='member@example.test',
            is_active=True,
        )
        self.card = MemberCard.objects.create(member=self.member, card_number='CARD001')
        self.wallet = get_or_create_wallet(self.member)

    def refresh_balance(self):
        self.wallet.refresh_from_db()
        return self.wallet.balance

    def assertBalance(self, expected):
        self.assertEqual(self.refresh_balance(), Decimal(expected))

    def test_approve_member_topup_credits_wallet_once(self):
        topup = request_member_topup(
            member=self.member,
            amount=Decimal('25000.00'),
            requested_by=self.member_user,
            note='Topup request',
        )

        approve_topup(topup=topup, validated_by=self.admin, validation_note='Valid')

        self.assertBalance('25000.00')
        ledger = MemberLedger.objects.get(topup=topup)
        self.assertEqual(ledger.txn_type, MemberLedger.TYPE_TOPUP)
        self.assertEqual(ledger.balance_before, Decimal('0.00'))
        self.assertEqual(ledger.balance_after, Decimal('25000.00'))
        self.assertEqual(ledger.ledger_key, f'TOPUP:{topup.id}')
        self.assertTrue(
            MemberDepositAuditLog.objects.filter(
                action=MemberDepositAuditLog.ACTION_TOPUP_REQUEST,
                topup=topup,
                actor=self.member_user,
            ).exists()
        )
        self.assertTrue(
            MemberDepositAuditLog.objects.filter(
                action=MemberDepositAuditLog.ACTION_TOPUP_APPROVE,
                topup=topup,
                ledger=ledger,
                actor=self.admin,
                balance_before=Decimal('0.00'),
                balance_after=Decimal('25000.00'),
            ).exists()
        )

        with self.assertRaisesMessage(ValidationError, 'Topup bukan status pending.'):
            approve_topup(topup=topup, validated_by=self.admin)

        self.assertBalance('25000.00')
        self.assertEqual(MemberLedger.objects.filter(topup=topup).count(), 1)

    def test_admin_topup_and_reversal_are_idempotency_guarded(self):
        topup = create_admin_topup(
            member=self.member,
            amount=Decimal('50000.00'),
            created_by=self.admin,
            note='Admin topup',
        )

        self.assertBalance('50000.00')
        reversal = reverse_topup(topup=topup, admin_user=self.admin, note='Correction')

        self.assertBalance('0.00')
        topup.refresh_from_db()
        self.assertEqual(topup.status, topup.STATUS_REVERSED)
        ledger = MemberLedger.objects.get(topup=reversal)
        self.assertEqual(ledger.txn_type, MemberLedger.TYPE_REVERSAL_TOPUP)
        self.assertEqual(ledger.ledger_key, f'REV-TOPUP:{topup.id}')
        self.assertTrue(
            MemberDepositAuditLog.objects.filter(
                action=MemberDepositAuditLog.ACTION_TOPUP_REVERSAL,
                topup=reversal,
                ledger=ledger,
                actor=self.admin,
            ).exists()
        )

        with self.assertRaisesMessage(ValidationError, 'Hanya topup approved yang bisa direversal.'):
            reverse_topup(topup=topup, admin_user=self.admin)

        self.assertBalance('0.00')
        self.assertEqual(
            MemberLedger.objects.filter(txn_type=MemberLedger.TYPE_REVERSAL_TOPUP).count(),
            1,
        )

    def test_topup_reversal_requires_enough_balance(self):
        topup = create_admin_topup(
            member=self.member,
            amount=Decimal('30000.00'),
            created_by=self.admin,
        )
        charge_member_by_card(
            card_number=self.card.card_number,
            amount=Decimal('20000.00'),
            reference_code='SALE-LOWBAL',
            description='POS test',
        )

        with self.assertRaisesMessage(ValidationError, 'Saldo member tidak mencukupi.'):
            reverse_topup(topup=topup, admin_user=self.admin)

        self.assertBalance('10000.00')
        topup.refresh_from_db()
        self.assertEqual(topup.status, topup.STATUS_APPROVED)
        self.assertFalse(topup.reversal_entries.exists())

    def test_withdrawal_debits_wallet_and_reversal_restores_balance(self):
        create_admin_topup(member=self.member, amount=Decimal('75000.00'), created_by=self.admin)

        withdrawal = create_admin_withdrawal(
            member=self.member,
            amount=Decimal('20000.00'),
            member_password='member-pass',
            created_by=self.admin,
            note='Tarik tunai',
        )

        self.assertBalance('55000.00')
        ledger = MemberLedger.objects.get(withdrawal=withdrawal)
        self.assertEqual(ledger.txn_type, MemberLedger.TYPE_WITHDRAWAL)
        self.assertEqual(ledger.ledger_key, f'WITHDRAWAL:{withdrawal.id}')
        self.assertTrue(
            MemberDepositAuditLog.objects.filter(
                action=MemberDepositAuditLog.ACTION_WITHDRAWAL,
                withdrawal=withdrawal,
                ledger=ledger,
                actor=self.admin,
            ).exists()
        )

        reversal = reverse_withdrawal(withdrawal=withdrawal, admin_user=self.admin)

        self.assertBalance('75000.00')
        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.status, withdrawal.STATUS_REVERSED)
        reversal_ledger = MemberLedger.objects.get(withdrawal=reversal)
        self.assertEqual(reversal_ledger.txn_type, MemberLedger.TYPE_REVERSAL_WITHDRAWAL)
        self.assertEqual(reversal_ledger.ledger_key, f'REV-WITHDRAWAL:{withdrawal.id}')
        self.assertTrue(
            MemberDepositAuditLog.objects.filter(
                action=MemberDepositAuditLog.ACTION_WITHDRAWAL_REVERSAL,
                withdrawal=reversal,
                ledger=reversal_ledger,
                actor=self.admin,
            ).exists()
        )

        with self.assertRaisesMessage(ValidationError, 'Hanya withdrawal approved yang bisa direversal.'):
            reverse_withdrawal(withdrawal=withdrawal, admin_user=self.admin)

        self.assertBalance('75000.00')
        self.assertEqual(
            MemberLedger.objects.filter(txn_type=MemberLedger.TYPE_REVERSAL_WITHDRAWAL).count(),
            1,
        )

    def test_withdrawal_requires_member_password(self):
        create_admin_topup(member=self.member, amount=Decimal('15000.00'), created_by=self.admin)

        with self.assertRaisesMessage(ValidationError, 'Password member tidak sesuai.'):
            create_admin_withdrawal(
                member=self.member,
                amount=Decimal('5000.00'),
                member_password='wrong-pass',
                created_by=self.admin,
            )

        self.assertBalance('15000.00')
        self.assertEqual(MemberLedger.objects.filter(txn_type=MemberLedger.TYPE_WITHDRAWAL).count(), 0)

    def test_pos_deposit_charge_is_idempotency_guarded_by_reference(self):
        create_admin_topup(member=self.member, amount=Decimal('40000.00'), created_by=self.admin)

        charge_member_by_card(
            card_number=self.card.card_number,
            amount=Decimal('12000.00'),
            reference_code='SALE-0001',
            description='Pembayaran POS',
        )

        self.assertBalance('28000.00')
        self.assertTrue(
            MemberDepositAuditLog.objects.filter(
                action=MemberDepositAuditLog.ACTION_POS_DEBIT,
                ledger__ledger_key='POS:SALE-0001',
                balance_before=Decimal('40000.00'),
                balance_after=Decimal('28000.00'),
            ).exists()
        )
        with self.assertRaisesMessage(ValidationError, 'Transaksi saldo ini sudah pernah diproses.'):
            charge_member_by_card(
                card_number=self.card.card_number,
                amount=Decimal('12000.00'),
                reference_code='SALE-0001',
                description='Double submit POS',
            )

        self.assertBalance('28000.00')
        self.assertEqual(MemberLedger.objects.filter(ledger_key='POS:SALE-0001').count(), 1)

    def test_pos_deposit_charge_prevents_negative_balance(self):
        create_admin_topup(member=self.member, amount=Decimal('8000.00'), created_by=self.admin)

        with self.assertRaisesMessage(ValidationError, 'Saldo member tidak mencukupi.'):
            charge_member_by_card(
                card_number=self.card.card_number,
                amount=Decimal('9000.00'),
                reference_code='SALE-OVER',
                description='Over balance',
            )

        self.assertBalance('8000.00')
        self.assertFalse(MemberLedger.objects.filter(ledger_key='POS:SALE-OVER').exists())


class MemberCardPrintViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        from core.constants import Role

        self.client = Client()
        self.admin_group, _ = Group.objects.get_or_create(name=Role.ADMIN_TOKO)
        self.admin = User.objects.create_user(username='admin_print', password='admin-pass')
        self.admin.groups.add(self.admin_group)

        self.member = Member.objects.create(
            code='MBR-007',
            full_name='Bagus Prasojo',
            phone='081299887766',
            is_active=True,
        )

    def test_member_card_print_with_existing_card(self):
        self.client.force_login(self.admin)
        MemberCard.objects.create(member=self.member, card_number='CRD-BAGUS-007')
        from django.urls import reverse
        url = reverse('member_card_print', kwargs={'uuid': self.member.uuid})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Bagus Prasojo')
        self.assertContains(resp, 'CRD-BAGUS-007')
        self.assertContains(resp, '<svg')

    def test_member_card_print_auto_creates_card_if_missing(self):
        self.client.force_login(self.admin)
        from django.urls import reverse
        url = reverse('member_card_print', kwargs={'uuid': self.member.uuid})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.member.refresh_from_db()
        self.assertEqual(self.member.card.card_number, 'MBR-007')
        self.assertContains(resp, 'MBR-007')
        self.assertContains(resp, '<svg')


class MemberDepositReportTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group, Permission
        from django.contrib.contenttypes.models import ContentType
        from core.constants import Role

        self.client = Client()
        self.admin_group, _ = Group.objects.get_or_create(name=Role.ADMIN_TOKO)
        self.member_group, _ = Group.objects.get_or_create(name=Role.MEMBER)

        # Grant view_members permission to admin group
        ct, _ = ContentType.objects.get_or_create(app_label='core', model='appaccess')
        perm, _ = Permission.objects.get_or_create(codename='view_members', content_type=ct, defaults={'name': 'View members'})
        self.admin_group.permissions.add(perm)

        self.admin = User.objects.create_user(username='admin_deposit', password='admin-pass')
        self.admin.groups.add(self.admin_group)

        self.normal_member_user = User.objects.create_user(username='member_only', password='member-pass')
        self.normal_member_user.groups.add(self.member_group)

        # Create members
        self.m1 = Member.objects.create(code='MBR-01', full_name='Budi Santoso', phone='0811111111', is_active=True)
        self.w1 = get_or_create_wallet(self.m1)
        self.c1 = MemberCard.objects.create(member=self.m1, card_number='CRD-01')

        self.u2 = User.objects.create_user(username='MBR-02', password='pass-m2')
        self.m2 = Member.objects.create(code='MBR-02', user=self.u2, full_name='Siti Rahma', phone='0822222222', is_active=True)
        self.w2 = get_or_create_wallet(self.m2)
        self.c2 = MemberCard.objects.create(member=self.m2, card_number='CRD-02')

        # Add transactions
        create_admin_topup(member=self.m1, amount=Decimal('100000.00'), created_by=self.admin, note='Topup 1')
        charge_member_by_card(card_number=self.c1.card_number, amount=Decimal('30000.00'), reference_code='SALE-01')

        create_admin_topup(member=self.m2, amount=Decimal('50000.00'), created_by=self.admin, note='Topup 2')
        create_admin_withdrawal(member=self.m2, amount=Decimal('10000.00'), member_password='pass-m2', created_by=self.admin, note='Tarik tunai')

    def test_anonymous_redirects_to_login(self):
        from django.urls import reverse
        resp = self.client.get(reverse('member_deposit_report'))
        self.assertEqual(resp.status_code, 302)

    def test_member_role_forbidden(self):
        from django.urls import reverse
        self.client.force_login(self.normal_member_user)
        resp = self.client.get(reverse('member_deposit_report'))
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_view_deposit_report_and_kpi(self):
        from django.urls import reverse
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('member_deposit_report'))
        self.assertEqual(resp.status_code, 200)

        # Check KPI values in context
        kpi = resp.context['kpi']
        self.assertEqual(kpi['total_wallet_pool'], Decimal('110000.00'))  # 70000 + 40000
        self.assertEqual(kpi['period_total_topup'], Decimal('150000.00'))
        self.assertEqual(kpi['period_total_purchase'], Decimal('30000.00'))
        self.assertEqual(kpi['period_total_withdrawal'], Decimal('10000.00'))
        self.assertEqual(kpi['period_net_flow'], Decimal('110000.00'))
        self.assertTrue(kpi['is_reconciled'])

        # Check content rendered in HTML
        self.assertContains(resp, 'Laporan Rekapitulasi &amp; Saldo Deposit Member')
        self.assertContains(resp, 'Budi Santoso')
        self.assertContains(resp, 'Siti Rahma')
        self.assertContains(resp, '100% Klop / Terverifikasi')

    def test_deposit_report_search_query(self):
        from django.urls import reverse
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('member_deposit_report') + '?q=Budi')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Budi Santoso')
        self.assertNotContains(resp, 'Siti Rahma')

    def test_deposit_report_export_csv(self):
        from django.urls import reverse
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('member_deposit_report_export_csv'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/csv', resp['Content-Type'])
        content = resp.content.decode('utf-8-sig')
        self.assertIn('LAPORAN REKAPITULASI & SALDO DEPOSIT MEMBER', content)
        self.assertIn('Budi Santoso', content)
        self.assertIn('Siti Rahma', content)
        self.assertIn('110,000.00', content)


class MemberCardBulkPrintTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group, Permission
        from django.contrib.contenttypes.models import ContentType
        from core.constants import Role

        self.client = Client()
        self.admin_group, _ = Group.objects.get_or_create(name=Role.ADMIN_TOKO)
        self.member_group, _ = Group.objects.get_or_create(name=Role.MEMBER)

        ct, _ = ContentType.objects.get_or_create(app_label='core', model='appaccess')
        perm, _ = Permission.objects.get_or_create(codename='view_members', content_type=ct, defaults={'name': 'View members'})
        self.admin_group.permissions.add(perm)

        self.admin = User.objects.create_user(username='admin_bulk_card', password='admin-pass')
        self.admin.groups.add(self.admin_group)

        self.normal_member_user = User.objects.create_user(username='normal_user', password='pass')
        self.normal_member_user.groups.add(self.member_group)

        self.m1 = Member.objects.create(code='MBR-001', full_name='Ahmad Dahlan', phone='08111222333', is_active=True)
        self.m2 = Member.objects.create(code='MBR-002', full_name='Fatmawati Sukarno', phone='08222333444', is_active=True)
        self.m3 = Member.objects.create(code='MBR-003', full_name='Ki Hajar Dewantara', phone='08333444555', is_active=False)

    def test_bulk_print_requires_login(self):
        from django.urls import reverse
        resp = self.client.get(reverse('member_card_bulk_print'))
        self.assertEqual(resp.status_code, 302)

    def test_bulk_print_forbidden_for_member_role(self):
        from django.urls import reverse
        self.client.force_login(self.normal_member_user)
        resp = self.client.get(reverse('member_card_bulk_print'))
        self.assertEqual(resp.status_code, 403)

    def test_bulk_print_with_selected_uuids_post(self):
        from django.urls import reverse
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('member_card_bulk_print'), {
            'member_uuids': [str(self.m1.uuid), str(self.m2.uuid)]
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Ahmad Dahlan')
        self.assertContains(resp, 'Fatmawati Sukarno')
        self.assertNotContains(resp, 'Ki Hajar Dewantara')
        self.assertContains(resp, 'Cetak Semua Kartu (2)')

        # Ensure cards were auto-created
        self.m1.refresh_from_db()
        self.m2.refresh_from_db()
        self.assertIsNotNone(self.m1.card)
        self.assertIsNotNone(self.m2.card)

    def test_bulk_print_with_query_param(self):
        from django.urls import reverse
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('member_card_bulk_print') + '?q=Fatmawati')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Fatmawati Sukarno')
        self.assertNotContains(resp, 'Ahmad Dahlan')

    def test_bulk_print_empty_selection_redirects(self):
        from django.urls import reverse
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('member_card_bulk_print') + '?q=NotExistentPersonXYZ')
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse('member_list'))



