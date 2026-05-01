from django.shortcuts import render

from core.decorators import role_required
from .models import Product


@role_required('admin_toko', 'kasir')
def product_list(request):
    products = Product.objects.select_related('category').order_by('name')
    return render(request, 'inventory/product_list.html', {'products': products})


@role_required('admin_toko', 'pembelian')
def purchase_page(request):
    return render(request, 'inventory/purchase_page.html')
