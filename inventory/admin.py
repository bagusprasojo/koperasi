from django.contrib import admin
from .models import Category, Product, ProductPriceTier


class ProductPriceTierInline(admin.TabularInline):
    model = ProductPriceTier
    extra = 3


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'sku', 'stock']
    search_fields = ['name', 'sku']
    inlines = [ProductPriceTierInline]


@admin.register(ProductPriceTier)
class ProductPriceTierAdmin(admin.ModelAdmin):
    list_display = [
        'product',
        'level',
        'min_qty',
        'max_qty',
        'price'
    ]
