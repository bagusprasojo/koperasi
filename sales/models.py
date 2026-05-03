from django.conf import settings
from django.db import models

from core.models import BaseModel
from inventory.models import Product
from members.models import Member


class Sale(BaseModel):
    sale_number = models.CharField(max_length=50, unique=True)
    client_txn_id = models.CharField(max_length=80, unique=True, null=True, blank=True, db_index=True)
    member = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name='sales')
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sales_created',
    )

    def __str__(self):
        return self.sale_number


class SaleItem(BaseModel):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    qty = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)


class SalePayment(BaseModel):
    METHOD_CASH = 'cash'
    METHOD_MEMBER = 'member_deposit'
    METHOD_CHOICES = (
        (METHOD_CASH, 'Cash'),
        (METHOD_MEMBER, 'Member Deposit'),
    )

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='payments')
    method = models.CharField(max_length=30, choices=METHOD_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=100, blank=True, default='')


class ReceiptPrintJob(BaseModel):
    STATUS_PENDING = 'pending'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'Pending'),
        (STATUS_SENT, 'Sent'),
        (STATUS_FAILED, 'Failed'),
    )

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='print_jobs')
    job_id = models.CharField(max_length=60, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    attempts = models.PositiveIntegerField(default=0)
    copies = models.PositiveIntegerField(default=1)
    bridge_url = models.CharField(max_length=255, default='http://127.0.0.1:17971/print')
    payload_json = models.TextField(default='')
    response_json = models.TextField(default='')
    last_error = models.CharField(max_length=255, blank=True, default='')
