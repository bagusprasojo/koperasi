from django.conf import settings
from django.db import models

from core.models import BaseModel
from inventory.models import Product
from members.models import Member


class Sale(BaseModel):
    sale_number = models.CharField(max_length=50, unique=True)
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
