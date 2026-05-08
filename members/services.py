from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from uuid import uuid4

from inventory.models import DailyClosing

from .models import Member, MemberCard, MemberLedger, MemberTopUp, MemberWallet, MemberWithdrawal


def get_or_create_wallet(member: Member) -> MemberWallet:
    wallet, _ = MemberWallet.objects.get_or_create(member=member, defaults={'balance': Decimal('0.00')})
    return wallet


def _generate_topup_number(prefix='TPU'):
    return f'{prefix}-{timezone.now().strftime("%Y%m%d")}-{uuid4().hex[:8].upper()}'


def _generate_withdrawal_number(prefix='WDR'):
    return f'{prefix}-{timezone.now().strftime("%Y%m%d")}-{uuid4().hex[:8].upper()}'


def _ensure_not_closed_today():
    today = timezone.localdate()
    if DailyClosing.objects.filter(close_date=today, is_locked=True).exists():
        raise ValidationError('Transaksi saldo tidak bisa diproses karena hari ini sudah tutup harian.')


def request_member_topup(member: Member, amount: Decimal, requested_by=None, note: str = '', proof_file=None) -> MemberTopUp:
    if amount <= 0:
        raise ValidationError('Nominal topup harus lebih besar dari 0.')
    return MemberTopUp.objects.create(
        member=member,
        topup_number=_generate_topup_number('TPM'),
        topup_type=MemberTopUp.TOPUP_TYPE_MEMBER,
        amount=amount,
        note=note,
        kind=MemberTopUp.KIND_MEMBER_REQUEST,
        status=MemberTopUp.STATUS_PENDING,
        requested_by=requested_by,
        proof_file=proof_file,
    )


def _apply_credit(member: Member, amount: Decimal, topup: MemberTopUp, description: str):
    _ensure_not_closed_today()
    wallet = get_or_create_wallet(member)
    before = wallet.balance
    after = before + amount
    wallet.balance = after
    wallet.save(update_fields=['balance', 'updated_at'])
    card = getattr(member, 'card', None)
    MemberLedger.objects.create(
        member=member,
        card=card,
        topup=topup,
        txn_type=MemberLedger.TYPE_TOPUP,
        amount=amount,
        balance_before=before,
        balance_after=after,
        description=description,
    )


@transaction.atomic
def approve_topup(topup: MemberTopUp, validated_by, validation_note: str = '') -> MemberTopUp:
    if topup.status != MemberTopUp.STATUS_PENDING:
        raise ValidationError('Topup bukan status pending.')
    now = timezone.now()
    topup.status = MemberTopUp.STATUS_APPROVED
    topup.validated_by = validated_by
    topup.validated_at = now
    topup.effective_at = now
    topup.validation_note = validation_note
    topup.save()
    _apply_credit(
        member=topup.member,
        amount=topup.amount,
        topup=topup,
        description=validation_note or 'Topup member tervalidasi admin',
    )
    return topup


def reject_topup(topup: MemberTopUp, validated_by, validation_note: str = '') -> MemberTopUp:
    if topup.status != MemberTopUp.STATUS_PENDING:
        raise ValidationError('Topup bukan status pending.')
    topup.status = MemberTopUp.STATUS_REJECTED
    topup.validated_by = validated_by
    topup.validated_at = timezone.now()
    topup.validation_note = validation_note
    topup.save()
    return topup


@transaction.atomic
def create_admin_topup(member: Member, amount: Decimal, created_by, note: str = '', kind: str = MemberTopUp.KIND_ADMIN_DIRECT):
    if amount <= 0:
        raise ValidationError('Nominal topup harus lebih besar dari 0.')
    now = timezone.now()
    topup = MemberTopUp.objects.create(
        member=member,
        topup_number=_generate_topup_number('TPA'),
        topup_type=MemberTopUp.TOPUP_TYPE_ADMIN,
        amount=amount,
        note=note,
        kind=kind,
        status=MemberTopUp.STATUS_APPROVED,
        created_by=created_by,
        requested_by=created_by,
        validated_by=created_by,
        validated_at=now,
        effective_at=now,
        validation_note='Topup langsung oleh admin',
    )
    _apply_credit(member=member, amount=amount, topup=topup, description=note or 'Topup langsung admin')
    return topup


def bulk_admin_topup(items, created_by):
    results = []
    for item in items:
        try:
            member = item['member']
            amount = item['amount']
            note = item.get('note', '')
            create_admin_topup(member=member, amount=amount, created_by=created_by, note=note, kind=MemberTopUp.KIND_ADMIN_BULK)
            results.append((member, True, 'OK'))
        except Exception as exc:
            results.append((item.get('member'), False, str(exc)))
    return results


@transaction.atomic
def reverse_topup(topup: MemberTopUp, admin_user, note: str = '') -> MemberTopUp:
    _ensure_not_closed_today()
    if topup.status != MemberTopUp.STATUS_APPROVED:
        raise ValidationError('Hanya topup approved yang bisa direversal.')
    if topup.kind == MemberTopUp.KIND_REVERSAL:
        raise ValidationError('Topup reversal tidak bisa direversal lagi.')
    if topup.reversal_entries.exists():
        raise ValidationError('Topup ini sudah pernah direversal.')

    wallet = get_or_create_wallet(topup.member)
    before = wallet.balance
    if before < topup.amount:
        raise ValidationError('Saldo member tidak cukup untuk reversal topup.')
    after = before - topup.amount
    wallet.balance = after
    wallet.save(update_fields=['balance', 'updated_at'])

    now = timezone.now()
    reversal = MemberTopUp.objects.create(
        member=topup.member,
        topup_number=_generate_topup_number('TPR'),
        topup_type=MemberTopUp.TOPUP_TYPE_ADMIN,
        amount=topup.amount,
        note=note,
        kind=MemberTopUp.KIND_REVERSAL,
        status=MemberTopUp.STATUS_APPROVED,
        created_by=admin_user,
        requested_by=admin_user,
        validated_by=admin_user,
        validated_at=now,
        effective_at=now,
        validation_note='Reversal topup oleh admin',
        reversal_of=topup,
    )
    card = getattr(topup.member, 'card', None)
    MemberLedger.objects.create(
        member=topup.member,
        card=card,
        topup=reversal,
        txn_type=MemberLedger.TYPE_REVERSAL_TOPUP,
        amount=topup.amount,
        balance_before=before,
        balance_after=after,
        description=note or f'Reversal topup {topup.id}',
        reference_code=f'REV-{topup.id}',
    )
    topup.status = MemberTopUp.STATUS_REVERSED
    topup.save(update_fields=['status', 'updated_at'])
    return reversal


@transaction.atomic
def charge_member_by_card(card_number: str, amount: Decimal, reference_code: str = '', description: str = '') -> MemberLedger:
    _ensure_not_closed_today()
    if amount <= 0:
        raise ValidationError('Nominal pembayaran harus lebih besar dari 0.')

    card = MemberCard.objects.select_related('member').filter(card_number=card_number).first()
    if not card:
        raise ValidationError('Kartu member tidak ditemukan.')
    if card.status != MemberCard.STATUS_ACTIVE:
        raise ValidationError('Kartu member tidak aktif.')
    if not card.member.is_active:
        raise ValidationError('Member tidak aktif.')

    wallet = get_or_create_wallet(card.member)
    before = wallet.balance
    if before < amount:
        raise ValidationError('Saldo member tidak mencukupi.')
    after = before - amount
    wallet.balance = after
    wallet.save(update_fields=['balance', 'updated_at'])

    return MemberLedger.objects.create(
        member=card.member,
        card=card,
        txn_type=MemberLedger.TYPE_PURCHASE,
        amount=amount,
        balance_before=before,
        balance_after=after,
        reference_code=reference_code,
        description=description or 'Pembayaran belanja member',
    )


@transaction.atomic
def create_admin_withdrawal(member: Member, amount: Decimal, member_password: str, created_by, note: str = '') -> MemberWithdrawal:
    _ensure_not_closed_today()
    if amount <= 0:
        raise ValidationError('Nominal tarik deposit harus lebih besar dari 0.')
    if not member.is_active:
        raise ValidationError('Member tidak aktif.')
    if not member.user:
        raise ValidationError('Member belum memiliki akun user untuk otorisasi.')
    if not member.user.check_password(member_password or ''):
        raise ValidationError('Password member tidak sesuai.')

    wallet = get_or_create_wallet(member)
    before = wallet.balance
    if before < amount:
        raise ValidationError('Saldo deposit member tidak mencukupi.')
    after = before - amount
    wallet.balance = after
    wallet.save(update_fields=['balance', 'updated_at'])

    wd = MemberWithdrawal.objects.create(
        member=member,
        withdrawal_number=_generate_withdrawal_number('WDA'),
        amount=amount,
        note=note,
        status=MemberWithdrawal.STATUS_APPROVED,
        created_by=created_by,
    )
    card = getattr(member, 'card', None)
    MemberLedger.objects.create(
        member=member,
        card=card,
        withdrawal=wd,
        txn_type=MemberLedger.TYPE_WITHDRAWAL,
        amount=amount,
        balance_before=before,
        balance_after=after,
        reference_code=wd.withdrawal_number,
        description=note or 'Penarikan deposit oleh admin',
    )
    return wd


@transaction.atomic
def reverse_withdrawal(withdrawal: MemberWithdrawal, admin_user, note: str = '') -> MemberWithdrawal:
    _ensure_not_closed_today()
    if withdrawal.status != MemberWithdrawal.STATUS_APPROVED:
        raise ValidationError('Hanya withdrawal approved yang bisa direversal.')
    if withdrawal.reversal_entries.exists():
        raise ValidationError('Withdrawal ini sudah pernah direversal.')

    wallet = get_or_create_wallet(withdrawal.member)
    before = wallet.balance
    after = before + withdrawal.amount
    wallet.balance = after
    wallet.save(update_fields=['balance', 'updated_at'])

    reversal = MemberWithdrawal.objects.create(
        member=withdrawal.member,
        withdrawal_number=_generate_withdrawal_number('WDR'),
        amount=withdrawal.amount,
        note=note,
        status=MemberWithdrawal.STATUS_APPROVED,
        created_by=admin_user,
        reversal_of=withdrawal,
    )
    card = getattr(withdrawal.member, 'card', None)
    MemberLedger.objects.create(
        member=withdrawal.member,
        card=card,
        withdrawal=reversal,
        txn_type=MemberLedger.TYPE_REVERSAL_WITHDRAWAL,
        amount=withdrawal.amount,
        balance_before=before,
        balance_after=after,
        reference_code=reversal.withdrawal_number,
        description=note or f'Reversal withdrawal {withdrawal.withdrawal_number}',
    )

    withdrawal.status = MemberWithdrawal.STATUS_REVERSED
    withdrawal.reversed_by = admin_user
    withdrawal.reversed_at = timezone.now()
    withdrawal.save(update_fields=['status', 'reversed_by', 'reversed_at', 'updated_at'])
    return reversal
