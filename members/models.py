from django.conf import settings
from django.db import models

from core.models import BaseModel


class Member(BaseModel):
    code = models.CharField(max_length=30, unique=True, null=True, blank=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='member_profile',
    )
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, unique=True)
    email = models.EmailField(blank=True, default='')
    address = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.full_name


class MemberCard(BaseModel):
    STATUS_ACTIVE = 'active'
    STATUS_BLOCKED = 'blocked'
    STATUS_EXPIRED = 'expired'
    STATUS_CHOICES = (
        (STATUS_ACTIVE, 'Active'),
        (STATUS_BLOCKED, 'Blocked'),
        (STATUS_EXPIRED, 'Expired'),
    )

    member = models.OneToOneField(
        Member,
        on_delete=models.CASCADE,
        related_name='card',
    )
    card_number = models.CharField(max_length=50, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    issued_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.card_number


class MemberWallet(BaseModel):
    member = models.OneToOneField(
        Member,
        on_delete=models.CASCADE,
        related_name='wallet',
    )
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    def __str__(self):
        return f'{self.member.full_name} Wallet'


class MemberTopUp(BaseModel):
    KIND_MEMBER_REQUEST = 'member_request'
    KIND_ADMIN_DIRECT = 'admin_direct'
    KIND_ADMIN_BULK = 'admin_bulk'
    KIND_REVERSAL = 'reversal'
    KIND_CHOICES = (
        (KIND_MEMBER_REQUEST, 'Member Request'),
        (KIND_ADMIN_DIRECT, 'Admin Direct'),
        (KIND_ADMIN_BULK, 'Admin Bulk'),
        (KIND_REVERSAL, 'Reversal'),
    )
    TOPUP_TYPE_MEMBER = 'member'
    TOPUP_TYPE_ADMIN = 'admin'
    TOPUP_TYPE_SYSTEM = 'system'
    TOPUP_TYPE_CHOICES = (
        (TOPUP_TYPE_MEMBER, 'Member'),
        (TOPUP_TYPE_ADMIN, 'Admin'),
        (TOPUP_TYPE_SYSTEM, 'System'),
    )

    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_REVERSED = 'reversed'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
        (STATUS_REVERSED, 'Reversed'),
    )

    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name='topups')
    topup_number = models.CharField(max_length=40, unique=True, db_index=True, blank=True, default='')
    topup_type = models.CharField(max_length=20, choices=TOPUP_TYPE_CHOICES, default=TOPUP_TYPE_MEMBER)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    note = models.CharField(max_length=255, blank=True, default='')
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=KIND_MEMBER_REQUEST)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    proof_file = models.FileField(upload_to='topup_proofs/', null=True, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='member_topup_requests',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='member_topups',
    )
    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='member_topup_validations',
    )
    validated_at = models.DateTimeField(null=True, blank=True)
    effective_at = models.DateTimeField(null=True, blank=True)
    validation_note = models.CharField(max_length=255, blank=True, default='')
    reversal_of = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reversal_entries',
    )

    def __str__(self):
        return f'Topup {self.member.full_name} {self.amount}'


class MemberLedger(BaseModel):
    TYPE_TOPUP = 'topup'
    TYPE_PURCHASE = 'purchase'
    TYPE_ADJUSTMENT = 'adjustment'
    TYPE_REFUND = 'refund'
    TYPE_REVERSAL_TOPUP = 'reversal_topup'
    TYPE_WITHDRAWAL = 'withdrawal'
    TYPE_REVERSAL_WITHDRAWAL = 'reversal_withdrawal'
    TXN_TYPE_CHOICES = (
        (TYPE_TOPUP, 'Topup'),
        (TYPE_PURCHASE, 'Purchase'),
        (TYPE_ADJUSTMENT, 'Adjustment'),
        (TYPE_REFUND, 'Refund'),
        (TYPE_REVERSAL_TOPUP, 'Reversal Topup'),
        (TYPE_WITHDRAWAL, 'Withdrawal'),
        (TYPE_REVERSAL_WITHDRAWAL, 'Reversal Withdrawal'),
    )

    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name='ledger_entries')
    card = models.ForeignKey(
        MemberCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ledger_entries',
    )
    topup = models.OneToOneField(
        MemberTopUp,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ledger_entry',
    )
    withdrawal = models.OneToOneField(
        'MemberWithdrawal',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ledger_entry',
    )
    txn_type = models.CharField(max_length=20, choices=TXN_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    balance_before = models.DecimalField(max_digits=14, decimal_places=2)
    balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    reference_code = models.CharField(max_length=100, blank=True, default='')
    ledger_key = models.CharField(max_length=120, unique=True, null=True, blank=True)
    description = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.member.full_name} {self.txn_type} {self.amount}'


class MemberWithdrawal(BaseModel):
    STATUS_APPROVED = 'approved'
    STATUS_REVERSED = 'reversed'
    STATUS_CHOICES = (
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REVERSED, 'Reversed'),
    )

    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name='withdrawals')
    withdrawal_number = models.CharField(max_length=40, unique=True, db_index=True, blank=True, default='')
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    note = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_APPROVED)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='member_withdrawals',
    )
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='member_withdrawals_reversed',
    )
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_of = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reversal_entries',
    )

    def __str__(self):
        return f'Withdrawal {self.member.full_name} {self.amount}'


class MemberDepositAuditLog(BaseModel):
    ACTION_TOPUP_REQUEST = 'topup_request'
    ACTION_TOPUP_APPROVE = 'topup_approve'
    ACTION_TOPUP_REJECT = 'topup_reject'
    ACTION_ADMIN_TOPUP = 'admin_topup'
    ACTION_BULK_TOPUP = 'bulk_topup'
    ACTION_TOPUP_REVERSAL = 'topup_reversal'
    ACTION_POS_DEBIT = 'pos_debit'
    ACTION_WITHDRAWAL = 'withdrawal'
    ACTION_WITHDRAWAL_REVERSAL = 'withdrawal_reversal'
    ACTION_CHOICES = (
        (ACTION_TOPUP_REQUEST, 'Topup Request'),
        (ACTION_TOPUP_APPROVE, 'Topup Approve'),
        (ACTION_TOPUP_REJECT, 'Topup Reject'),
        (ACTION_ADMIN_TOPUP, 'Admin Topup'),
        (ACTION_BULK_TOPUP, 'Bulk Topup'),
        (ACTION_TOPUP_REVERSAL, 'Topup Reversal'),
        (ACTION_POS_DEBIT, 'POS Debit'),
        (ACTION_WITHDRAWAL, 'Withdrawal'),
        (ACTION_WITHDRAWAL_REVERSAL, 'Withdrawal Reversal'),
    )

    action = models.CharField(max_length=40, choices=ACTION_CHOICES, db_index=True)
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name='deposit_audit_logs')
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='member_deposit_audit_logs',
    )
    ledger = models.ForeignKey(
        MemberLedger,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
    )
    topup = models.ForeignKey(
        MemberTopUp,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
    )
    withdrawal = models.ForeignKey(
        MemberWithdrawal,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    balance_before = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    balance_after = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default='')
    note = models.CharField(max_length=255, blank=True, default='')
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.member.full_name} {self.action} {self.amount}'
