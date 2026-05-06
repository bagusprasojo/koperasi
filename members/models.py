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
    TXN_TYPE_CHOICES = (
        (TYPE_TOPUP, 'Topup'),
        (TYPE_PURCHASE, 'Purchase'),
        (TYPE_ADJUSTMENT, 'Adjustment'),
        (TYPE_REFUND, 'Refund'),
        (TYPE_REVERSAL_TOPUP, 'Reversal Topup'),
    )

    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name='ledger_entries')
    card = models.ForeignKey(
        MemberCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ledger_entries',
    )
    topup = models.ForeignKey(
        MemberTopUp,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ledger_entries',
    )
    txn_type = models.CharField(max_length=20, choices=TXN_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    balance_before = models.DecimalField(max_digits=14, decimal_places=2)
    balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    reference_code = models.CharField(max_length=100, blank=True, default='')
    description = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.member.full_name} {self.txn_type} {self.amount}'
