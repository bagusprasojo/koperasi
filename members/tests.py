from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

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
