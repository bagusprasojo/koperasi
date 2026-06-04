from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from uuid import uuid4

from inventory.models import DailyClosing

from .models import Member, MemberCard, MemberDepositAuditLog, MemberLedger, MemberTopUp, MemberWallet, MemberWithdrawal


def get_or_create_wallet(member: Member) -> MemberWallet:
    wallet, _ = MemberWallet.objects.get_or_create(member=member, defaults={'balance': Decimal('0.00')})
    return wallet


def _get_wallet_for_update(member: Member) -> MemberWallet:
    wallet = MemberWallet.objects.select_for_update().filter(member=member).first()
    if wallet:
        return wallet
    # Aman dipanggil dalam transaksi atomic; baris baru langsung di-lock ulang.
    get_or_create_wallet(member)
    return MemberWallet.objects.select_for_update().get(member=member)


def _generate_topup_number(prefix='TPU'):
    return f'{prefix}-{timezone.now().strftime("%Y%m%d")}-{uuid4().hex[:8].upper()}'


def _generate_withdrawal_number(prefix='WDR'):
    return f'{prefix}-{timezone.now().strftime("%Y%m%d")}-{uuid4().hex[:8].upper()}'


def _ledger_key(prefix: str, value) -> str:
    return f'{prefix}:{value}'


def _ensure_not_closed_today():
    today = timezone.localdate()
    if DailyClosing.objects.filter(close_date=today, is_locked=True).exists():
        raise ValidationError('Transaksi saldo tidak bisa diproses karena hari ini sudah tutup harian.')


def build_audit_context(request):
    forwarded_for = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    ip_address = forwarded_for or request.META.get('REMOTE_ADDR') or None
    return {
        'ip_address': ip_address,
        'user_agent': (request.META.get('HTTP_USER_AGENT') or '')[:255],
    }


def _write_deposit_audit(
    *,
    action: str,
    member: Member,
    actor=None,
    ledger=None,
    topup=None,
    withdrawal=None,
    amount=Decimal('0.00'),
    balance_before=None,
    balance_after=None,
    note: str = '',
    audit_context=None,
    metadata=None,
) -> MemberDepositAuditLog:
    audit_context = audit_context or {}
    return MemberDepositAuditLog.objects.create(
        action=action,
        member=member,
        actor=actor if getattr(actor, 'is_authenticated', True) else None,
        ledger=ledger,
        topup=topup,
        withdrawal=withdrawal,
        amount=amount or Decimal('0.00'),
        balance_before=balance_before,
        balance_after=balance_after,
        ip_address=audit_context.get('ip_address') or None,
        user_agent=(audit_context.get('user_agent') or '')[:255],
        note=note[:255],
        metadata=metadata or {},
    )


def _create_ledger_and_update_wallet(
    *,
    member: Member,
    txn_type: str,
    amount: Decimal,
    ledger_key: str,
    card=None,
    topup=None,
    withdrawal=None,
    reference_code: str = '',
    description: str = '',
    direction: str,
    audit_action: str,
    actor=None,
    audit_context=None,
    audit_metadata=None,
) -> MemberLedger:
    if amount <= 0:
        raise ValidationError('Nominal transaksi saldo harus lebih besar dari 0.')
    wallet = _get_wallet_for_update(member)
    if MemberLedger.objects.filter(ledger_key=ledger_key).exists():
        raise ValidationError('Transaksi saldo ini sudah pernah diproses.')

    before = wallet.balance
    if direction == 'in':
        after = before + amount
    elif direction == 'out':
        if before < amount:
            raise ValidationError('Saldo member tidak mencukupi.')
        after = before - amount
    else:
        raise ValidationError('Arah mutasi saldo tidak valid.')

    ledger = MemberLedger.objects.create(
        member=member,
        card=card,
        topup=topup,
        withdrawal=withdrawal,
        txn_type=txn_type,
        amount=amount,
        balance_before=before,
        balance_after=after,
        reference_code=reference_code,
        ledger_key=ledger_key,
        description=description,
    )
    wallet.balance = ledger.balance_after
    wallet.save(update_fields=['balance', 'updated_at'])
    _write_deposit_audit(
        action=audit_action,
        member=member,
        actor=actor,
        ledger=ledger,
        topup=topup,
        withdrawal=withdrawal,
        amount=amount,
        balance_before=before,
        balance_after=after,
        note=description,
        audit_context=audit_context,
        metadata=audit_metadata,
    )
    return ledger


def request_member_topup(member: Member, amount: Decimal, requested_by=None, note: str = '', proof_file=None, audit_context=None) -> MemberTopUp:
    if amount <= 0:
        raise ValidationError('Nominal topup harus lebih besar dari 0.')
    topup = MemberTopUp.objects.create(
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
    wallet = get_or_create_wallet(member)
    _write_deposit_audit(
        action=MemberDepositAuditLog.ACTION_TOPUP_REQUEST,
        member=member,
        actor=requested_by,
        topup=topup,
        amount=amount,
        balance_before=wallet.balance,
        balance_after=wallet.balance,
        note=note or 'Request topup member',
        audit_context=audit_context,
        metadata={'topup_number': topup.topup_number, 'status': topup.status},
    )
    return topup


def _apply_credit(member: Member, amount: Decimal, topup: MemberTopUp, description: str, actor=None, audit_context=None, audit_action=None):
    _ensure_not_closed_today()
    card = getattr(member, 'card', None)
    return _create_ledger_and_update_wallet(
        member=member,
        card=card,
        topup=topup,
        txn_type=MemberLedger.TYPE_TOPUP,
        amount=amount,
        ledger_key=_ledger_key('TOPUP', topup.id),
        description=description,
        direction='in',
        audit_action=audit_action or MemberDepositAuditLog.ACTION_TOPUP_APPROVE,
        actor=actor,
        audit_context=audit_context,
        audit_metadata={'topup_number': topup.topup_number, 'kind': topup.kind},
    )


@transaction.atomic
def approve_topup(topup: MemberTopUp, validated_by, validation_note: str = '', audit_context=None) -> MemberTopUp:
    topup = MemberTopUp.objects.select_for_update().select_related('member').get(pk=topup.pk)
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
        actor=validated_by,
        audit_context=audit_context,
        audit_action=MemberDepositAuditLog.ACTION_TOPUP_APPROVE,
    )
    return topup


@transaction.atomic
def reject_topup(topup: MemberTopUp, validated_by, validation_note: str = '', audit_context=None) -> MemberTopUp:
    topup = MemberTopUp.objects.select_for_update().get(pk=topup.pk)
    if topup.status != MemberTopUp.STATUS_PENDING:
        raise ValidationError('Topup bukan status pending.')
    topup.status = MemberTopUp.STATUS_REJECTED
    topup.validated_by = validated_by
    topup.validated_at = timezone.now()
    topup.validation_note = validation_note
    topup.save()
    wallet = get_or_create_wallet(topup.member)
    _write_deposit_audit(
        action=MemberDepositAuditLog.ACTION_TOPUP_REJECT,
        member=topup.member,
        actor=validated_by,
        topup=topup,
        amount=topup.amount,
        balance_before=wallet.balance,
        balance_after=wallet.balance,
        note=validation_note or 'Topup ditolak',
        audit_context=audit_context,
        metadata={'topup_number': topup.topup_number, 'status': topup.status},
    )
    return topup


@transaction.atomic
def create_admin_topup(member: Member, amount: Decimal, created_by, note: str = '', kind: str = MemberTopUp.KIND_ADMIN_DIRECT, audit_context=None):
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
    audit_action = MemberDepositAuditLog.ACTION_BULK_TOPUP if kind == MemberTopUp.KIND_ADMIN_BULK else MemberDepositAuditLog.ACTION_ADMIN_TOPUP
    _apply_credit(
        member=member,
        amount=amount,
        topup=topup,
        description=note or 'Topup langsung admin',
        actor=created_by,
        audit_context=audit_context,
        audit_action=audit_action,
    )
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
def reverse_topup(topup: MemberTopUp, admin_user, note: str = '', audit_context=None) -> MemberTopUp:
    topup = MemberTopUp.objects.select_for_update().select_related('member').get(pk=topup.pk)
    _ensure_not_closed_today()
    if topup.status != MemberTopUp.STATUS_APPROVED:
        raise ValidationError('Hanya topup approved yang bisa direversal.')
    if topup.kind == MemberTopUp.KIND_REVERSAL:
        raise ValidationError('Topup reversal tidak bisa direversal lagi.')
    if topup.reversal_entries.exists():
        raise ValidationError('Topup ini sudah pernah direversal.')

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
    _create_ledger_and_update_wallet(
        member=topup.member,
        card=card,
        topup=reversal,
        txn_type=MemberLedger.TYPE_REVERSAL_TOPUP,
        amount=topup.amount,
        ledger_key=_ledger_key('REV-TOPUP', topup.id),
        description=note or f'Reversal topup {topup.id}',
        reference_code=f'REV-{topup.id}',
        direction='out',
        audit_action=MemberDepositAuditLog.ACTION_TOPUP_REVERSAL,
        actor=admin_user,
        audit_context=audit_context,
        audit_metadata={'original_topup_number': topup.topup_number, 'reversal_topup_number': reversal.topup_number},
    )
    topup.status = MemberTopUp.STATUS_REVERSED
    topup.save(update_fields=['status', 'updated_at'])
    return reversal


@transaction.atomic
def charge_member_by_card(card_number: str, amount: Decimal, reference_code: str = '', description: str = '', actor=None, audit_context=None) -> MemberLedger:
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

    return _create_ledger_and_update_wallet(
        member=card.member,
        card=card,
        txn_type=MemberLedger.TYPE_PURCHASE,
        amount=amount,
        reference_code=reference_code,
        ledger_key=_ledger_key('POS', reference_code) if reference_code else _ledger_key('POS', uuid4().hex),
        description=description or 'Pembayaran belanja member',
        direction='out',
        audit_action=MemberDepositAuditLog.ACTION_POS_DEBIT,
        actor=actor,
        audit_context=audit_context,
        audit_metadata={'reference_code': reference_code, 'card_number': card.card_number},
    )


@transaction.atomic
def create_admin_withdrawal(member: Member, amount: Decimal, member_password: str, created_by, note: str = '', audit_context=None) -> MemberWithdrawal:
    _ensure_not_closed_today()
    if amount <= 0:
        raise ValidationError('Nominal tarik deposit harus lebih besar dari 0.')
    if not member.is_active:
        raise ValidationError('Member tidak aktif.')
    if not member.user:
        raise ValidationError('Member belum memiliki akun user untuk otorisasi.')
    if not member.user.check_password(member_password or ''):
        raise ValidationError('Password member tidak sesuai.')

    wd = MemberWithdrawal.objects.create(
        member=member,
        withdrawal_number=_generate_withdrawal_number('WDA'),
        amount=amount,
        note=note,
        status=MemberWithdrawal.STATUS_APPROVED,
        created_by=created_by,
    )
    card = getattr(member, 'card', None)
    _create_ledger_and_update_wallet(
        member=member,
        card=card,
        withdrawal=wd,
        txn_type=MemberLedger.TYPE_WITHDRAWAL,
        amount=amount,
        reference_code=wd.withdrawal_number,
        ledger_key=_ledger_key('WITHDRAWAL', wd.id),
        description=note or 'Penarikan deposit oleh admin',
        direction='out',
        audit_action=MemberDepositAuditLog.ACTION_WITHDRAWAL,
        actor=created_by,
        audit_context=audit_context,
        audit_metadata={'withdrawal_number': wd.withdrawal_number},
    )
    return wd


@transaction.atomic
def reverse_withdrawal(withdrawal: MemberWithdrawal, admin_user, note: str = '', audit_context=None) -> MemberWithdrawal:
    withdrawal = MemberWithdrawal.objects.select_for_update().select_related('member').get(pk=withdrawal.pk)
    _ensure_not_closed_today()
    if withdrawal.status != MemberWithdrawal.STATUS_APPROVED:
        raise ValidationError('Hanya withdrawal approved yang bisa direversal.')
    if withdrawal.reversal_entries.exists():
        raise ValidationError('Withdrawal ini sudah pernah direversal.')

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
    _create_ledger_and_update_wallet(
        member=withdrawal.member,
        card=card,
        withdrawal=reversal,
        txn_type=MemberLedger.TYPE_REVERSAL_WITHDRAWAL,
        amount=withdrawal.amount,
        reference_code=reversal.withdrawal_number,
        ledger_key=_ledger_key('REV-WITHDRAWAL', withdrawal.id),
        description=note or f'Reversal withdrawal {withdrawal.withdrawal_number}',
        direction='in',
        audit_action=MemberDepositAuditLog.ACTION_WITHDRAWAL_REVERSAL,
        actor=admin_user,
        audit_context=audit_context,
        audit_metadata={'original_withdrawal_number': withdrawal.withdrawal_number, 'reversal_withdrawal_number': reversal.withdrawal_number},
    )

    withdrawal.status = MemberWithdrawal.STATUS_REVERSED
    withdrawal.reversed_by = admin_user
    withdrawal.reversed_at = timezone.now()
    withdrawal.save(update_fields=['status', 'reversed_by', 'reversed_at', 'updated_at'])
    return reversal
