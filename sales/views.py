from django.shortcuts import render

from core.decorators import role_required
from inventory.models import Product


@role_required('kasir', 'admin_toko')
def pos_page(request):
    products = Product.objects.order_by('name')
    return render(request, 'sales/pos_page.html', {'products': products})
