from django.db import models
from django.conf import settings
from core.models import BaseModel
from django.core.exceptions import ValidationError
from members.models import Member


class Category(BaseModel):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Unit(BaseModel):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True)
    is_active = models.BooleanField(default=True)
    description = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.code})'


class Supplier(BaseModel):
    code = models.CharField(max_length=30, unique=True, null=True, blank=True)
    name = models.CharField(max_length=150, unique=True)
    contact_name = models.CharField(max_length=100, blank=True, default='')
    phone = models.CharField(max_length=30, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    address = models.CharField(max_length=255, blank=True, default='')
    city = models.CharField(max_length=100, blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='suppliers_created',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='suppliers_updated',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Product(BaseModel):
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE
    )
    unit = models.ForeignKey(
        Unit,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=150)
    sku = models.CharField(max_length=50, unique=True)
    barcode = models.CharField(max_length=80, unique=True, null=True, blank=True)
    stock = models.IntegerField(default=0)
    last_purchase_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reorder_point = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name


class ProductPriceTier(BaseModel):
    PRICE_MODE_CHOICES = (
        ('final', 'Harga Jadi'),
        ('discount', 'Diskon'),
    )
    DISCOUNT_TYPE_CHOICES = (
        ('percent', 'Persen'),
        ('nominal', 'Nominal'),
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='price_tiers'
    )
    level = models.PositiveSmallIntegerField()
    min_qty = models.PositiveIntegerField()
    max_qty = models.PositiveIntegerField()
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )
    source_mode = models.CharField(
        max_length=10,
        choices=PRICE_MODE_CHOICES,
        default='final',
    )
    discount_type = models.CharField(
        max_length=10,
        choices=DISCOUNT_TYPE_CHOICES,
        blank=True,
        default='',
    )
    discount_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ['level']
        unique_together = ('product', 'level')

    def clean(self):
        # Skip semua validasi DB kalau product belum disimpan
        if not self.product or not self.product.pk:
            return
        
        if self.min_qty > self.max_qty:
            raise ValidationError("min_qty tidak boleh lebih besar dari max_qty")

        qs = ProductPriceTier.objects.filter(product=self.product)

        if self.pk:
            qs = qs.exclude(pk=self.pk)

        # Cek overlap
        for tier in qs:
            if not (self.max_qty < tier.min_qty or self.min_qty > tier.max_qty):
                raise ValidationError(
                    f"Range {self.min_qty}-{self.max_qty} overlap dengan "
                    f"{tier.min_qty}-{tier.max_qty}"
                )

        # Optional: enforce level ordering
        lower_tiers = qs.filter(level__lt=self.level)
        higher_tiers = qs.filter(level__gt=self.level)

        if lower_tiers.exists():
            max_lower = max(t.max_qty for t in lower_tiers)
            if self.min_qty <= max_lower:
                raise ValidationError(
                    "Range harus lebih besar dari tier sebelumnya"
                )

        if higher_tiers.exists():
            min_higher = min(t.min_qty for t in higher_tiers)
            if self.max_qty >= min_higher:
                raise ValidationError(
                    "Range harus lebih kecil dari tier berikutnya"
                )

    def __str__(self):
        return f"{self.product.name} - Level {self.level}"
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class InventoryTransaction(BaseModel):
    TYPE_PURCHASE = 'purchase'
    TYPE_INTERNAL_USED = 'internal_used'
    TYPE_POS_SALE = 'pos_sale'
    TYPE_STOCK_OPNAME = 'stock_opname'
    TYPE_DAILY_CLOSING = 'daily_closing'
    TYPE_CHOICES = (
        (TYPE_PURCHASE, 'Purchase'),
        (TYPE_INTERNAL_USED, 'Internal Used'),
        (TYPE_POS_SALE, 'POS Sale'),
        (TYPE_STOCK_OPNAME, 'Stock Opname'),
        (TYPE_DAILY_CLOSING, 'Daily Closing'),
    )

    tx_number = models.CharField(max_length=40, unique=True, db_index=True)
    tx_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    tx_date = models.DateField()
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inventory_transactions',
    )
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reference = models.CharField(max_length=100, blank=True, default='')
    note = models.CharField(max_length=255, blank=True, default='')
    member = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name='inventory_transactions')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inventory_transactions',
    )

    class Meta:
        ordering = ['-tx_date', '-created_at']


class InventoryTransactionItem(BaseModel):
    transaction = models.ForeignKey(InventoryTransaction, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    qty = models.IntegerField()  # +in / -out
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)


class StockLedger(BaseModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_ledgers')
    tx = models.ForeignKey(InventoryTransaction, on_delete=models.CASCADE, related_name='stock_ledgers')
    tx_date = models.DateField()
    qty_in = models.IntegerField(default=0)
    qty_out = models.IntegerField(default=0)
    balance_before = models.IntegerField(default=0)
    balance_after = models.IntegerField(default=0)
    unit_cost_at_txn = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    value_in = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    value_out = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    note = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        ordering = ['tx_date', 'created_at']


class DailyClosing(BaseModel):
    close_date = models.DateField(unique=True, db_index=True)
    prev_close_date = models.DateField(null=True, blank=True)
    is_locked = models.BooleanField(default=False)
    note = models.CharField(max_length=255, blank=True, default='')
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='daily_closings',
    )


class ProductDailySnapshot(BaseModel):
    closing = models.ForeignKey(DailyClosing, on_delete=models.CASCADE, related_name='product_snapshots')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='daily_snapshots')
    opening_stock = models.IntegerField(default=0)
    mutation_in = models.IntegerField(default=0)
    mutation_out = models.IntegerField(default=0)
    closing_stock = models.IntegerField(default=0)

    class Meta:
        unique_together = ('closing', 'product')


class MemberDailySnapshot(BaseModel):
    closing = models.ForeignKey(DailyClosing, on_delete=models.CASCADE, related_name='member_snapshots')
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='daily_snapshots')
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    mutation_in = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    mutation_out = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    closing_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        unique_together = ('closing', 'member')
