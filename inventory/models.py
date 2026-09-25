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


class Consignor(BaseModel):
    code = models.CharField(max_length=30, unique=True, db_index=True, help_text="Kode unik penitip, misal: P01, IBU-SITI")
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True, default='')
    address = models.CharField(max_length=255, blank=True, default='')
    member = models.ForeignKey(
        Member,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='consignors',
        help_text="Tautkan jika penitip adalah anggota koperasi",
    )
    bank_name = models.CharField(max_length=50, blank=True, default='')
    bank_account = models.CharField(max_length=50, blank=True, default='')
    bank_account_holder = models.CharField(max_length=100, blank=True, default='')
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='consignors_created',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='consignors_updated',
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"


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
    stock = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    last_purchase_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cost_of_goods_sold = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reorder_point = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    allow_decimal_qty = models.BooleanField(default=False, verbose_name="Bisa Dijual Pecahan / Curah")
    is_consignment = models.BooleanField(default=False, verbose_name="Barang Titipan / Konsinyasi")
    consignor = models.ForeignKey(
        Consignor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products',
    )

    @property
    def clean_stock(self):
        d = Decimal(str(self.stock or 0))
        if d % Decimal('1') == Decimal('0'):
            return str(int(d))
        return f"{d:f}".rstrip('0').rstrip('.')

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
    min_qty = models.DecimalField(max_digits=12, decimal_places=3, default=1)
    max_qty = models.DecimalField(max_digits=12, decimal_places=3, default=999999)
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
    TYPE_CONSIGNMENT_IN = 'consignment_in'
    TYPE_CONSIGNMENT_RETURN = 'consignment_return'
    TYPE_CHOICES = (
        (TYPE_PURCHASE, 'Purchase'),
        (TYPE_INTERNAL_USED, 'Internal Used'),
        (TYPE_POS_SALE, 'POS Sale'),
        (TYPE_STOCK_OPNAME, 'Stock Opname'),
        (TYPE_DAILY_CLOSING, 'Daily Closing'),
        (TYPE_CONSIGNMENT_IN, 'Consignment Inflow (Titipan Pagi)'),
        (TYPE_CONSIGNMENT_RETURN, 'Consignment Return (Retur Sore)'),
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
    consignor = models.ForeignKey(
        Consignor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inventory_transactions',
    )
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reference = models.CharField(max_length=100, blank=True, default='')
    note = models.CharField(max_length=255, blank=True, default='')
    member = models.ForeignKey(Member, on_delete=models.PROTECT, null=True, blank=True, related_name='inventory_transactions')
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
    qty = models.DecimalField(max_digits=12, decimal_places=3)  # +in / -out
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)


class StockLedger(BaseModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_ledgers')
    tx = models.ForeignKey(InventoryTransaction, on_delete=models.CASCADE, related_name='stock_ledgers')
    tx_date = models.DateField()
    qty_in = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    qty_out = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    balance_before = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    balance_after = models.DecimalField(max_digits=12, decimal_places=3, default=0)
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
    opening_stock = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    mutation_in = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    mutation_out = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    closing_stock = models.DecimalField(max_digits=12, decimal_places=3, default=0)

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


class ConsignmentBatch(BaseModel):
    STATUS_OPEN = 'open'
    STATUS_SETTLED = 'settled'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = (
        (STATUS_OPEN, 'Aktif (Sedang Berjalan)'),
        (STATUS_SETTLED, 'Selesai (Sudah Rekap & Bayar)'),
        (STATUS_CANCELLED, 'Dibatalkan'),
    )

    PAYOUT_METHOD_CASH = 'cash'
    PAYOUT_METHOD_MEMBER_DEPOSIT = 'member_deposit'
    PAYOUT_METHOD_TRANSFER = 'transfer'
    PAYOUT_METHOD_CHOICES = (
        (PAYOUT_METHOD_CASH, 'Tunai (Kas Toko)'),
        (PAYOUT_METHOD_MEMBER_DEPOSIT, 'Deposit Dompet Anggota'),
        (PAYOUT_METHOD_TRANSFER, 'Transfer Bank'),
    )

    batch_number = models.CharField(max_length=40, unique=True, db_index=True)
    consignor = models.ForeignKey(Consignor, on_delete=models.PROTECT, related_name='batches')
    batch_date = models.DateField(db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)

    # Financial & Stock summaries
    total_received_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)  # Total HPP titipan masuk
    total_sold_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)        # Total hak bayar penitip (HPP * qty_sold)
    total_sold_retail = models.DecimalField(max_digits=14, decimal_places=2, default=0)      # Total omzet kasir (Jual * qty_sold)
    total_coop_margin = models.DecimalField(max_digits=14, decimal_places=2, default=0)      # Total margin koperasi

    payout_method = models.CharField(max_length=20, choices=PAYOUT_METHOD_CHOICES, blank=True, default='')
    payout_reference = models.CharField(max_length=100, blank=True, default='')
    settled_at = models.DateTimeField(null=True, blank=True)
    settled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='settled_consignments',
    )
    inflow_transaction = models.ForeignKey(
        InventoryTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='consignment_inflows',
    )
    return_transaction = models.ForeignKey(
        InventoryTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='consignment_returns',
    )
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_consignments',
    )

    class Meta:
        ordering = ['-batch_date', '-created_at']

    def __str__(self):
        return f"{self.batch_number} - {self.consignor.name} ({self.batch_date})"


class ConsignmentBatchItem(BaseModel):
    batch = models.ForeignKey(ConsignmentBatch, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='consignment_batch_items')
    cost_price = models.DecimalField(max_digits=14, decimal_places=2)  # Harga penitip (HPP)
    sale_price = models.DecimalField(max_digits=14, decimal_places=2)  # Harga jual toko
    qty_received = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    qty_sold = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    qty_returned = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    qty_loss = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    payable_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    coop_margin = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    notes = models.CharField(max_length=255, blank=True, default='')

    @property
    def subtotal_received(self):
        return (self.qty_received * self.cost_price).quantize(Decimal('0.01'))

    @property
    def clean_qty_received(self):
        d = Decimal(str(self.qty_received or 0))
        if d % Decimal('1') == Decimal('0'):
            return str(int(d))
        return f"{d:f}".rstrip('0').rstrip('.')

    @property
    def clean_qty_sold(self):
        d = Decimal(str(self.qty_sold or 0))
        if d % Decimal('1') == Decimal('0'):
            return str(int(d))
        return f"{d:f}".rstrip('0').rstrip('.')

    @property
    def clean_qty_returned(self):
        d = Decimal(str(self.qty_returned or 0))
        if d % Decimal('1') == Decimal('0'):
            return str(int(d))
        return f"{d:f}".rstrip('0').rstrip('.')

    @property
    def clean_qty_loss(self):
        d = Decimal(str(self.qty_loss or 0))
        if d % Decimal('1') == Decimal('0'):
            return str(int(d))
        return f"{d:f}".rstrip('0').rstrip('.')

    def __str__(self):
        return f"{self.batch.batch_number} - {self.product.name}"
