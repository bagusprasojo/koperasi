from django.contrib import admin
from .models import Category, Product, ProductPriceTier, Supplier
from django.forms.models import BaseInlineFormSet
from django.core.exceptions import ValidationError

class ProductPriceTierInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()

        ranges = []

        for form in self.forms:
            if not form.cleaned_data:
                continue

            min_qty = form.cleaned_data.get('min_qty')
            max_qty = form.cleaned_data.get('max_qty')

            if min_qty is None or max_qty is None:
                continue

            if min_qty > max_qty:
                raise ValidationError("min_qty tidak boleh lebih besar dari max_qty")

            # cek overlap antar form
            for existing in ranges:
                if not (max_qty < existing[0] or min_qty > existing[1]):
                    raise ValidationError(
                        f"Range {min_qty}-{max_qty} overlap dengan "
                        f"{existing[0]}-{existing[1]}"
                    )

            ranges.append((min_qty, max_qty))

class ProductPriceTierInline(admin.TabularInline):
    model = ProductPriceTier
    extra = 3
    formset = ProductPriceTierInlineFormSet

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


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'contact_name', 'phone', 'city', 'is_active']
    search_fields = ['code', 'name', 'contact_name', 'phone', 'email', 'city']
