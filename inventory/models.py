from django.db import models
from core.models import BaseModel
from django.core.exceptions import ValidationError


class Category(BaseModel):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Product(BaseModel):
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE
    )
    name = models.CharField(max_length=150)
    sku = models.CharField(max_length=50, unique=True)
    stock = models.IntegerField(default=0)

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
