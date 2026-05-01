from django.db import models
from core.models import BaseModel


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

    class Meta:
        ordering = ['level']
        unique_together = ('product', 'level')

    def __str__(self):
        return f"{self.product.name} - Level {self.level}"
