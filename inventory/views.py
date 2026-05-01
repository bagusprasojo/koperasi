from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from core.decorators import role_required
from .models import Category, Product


@role_required('admin_toko', 'kasir')
def product_list(request):
    products = Product.objects.select_related('category').order_by('name')
    return render(request, 'inventory/product_list.html', {'products': products})


@role_required('admin_toko', 'pembelian')
def purchase_page(request):
    return render(request, 'inventory/purchase_page.html')


@role_required('admin_toko', 'pembelian')
def category_list(request):
    query = request.GET.get('q', '').strip()
    categories = Category.objects.order_by('name')
    if query:
        categories = categories.filter(name__icontains=query)

    paginator = Paginator(categories, 10)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(
        request,
        'inventory/category_list.html',
        {'page_obj': page_obj, 'query': query},
    )


@role_required('admin_toko', 'pembelian')
def category_detail(request, uuid):
    category = get_object_or_404(Category, uuid=uuid)
    return render(request, 'inventory/category_detail.html', {'category': category})


@role_required('admin_toko', 'pembelian')
def category_edit(request, uuid):
    category = get_object_or_404(Category, uuid=uuid)
    error_message = ''
    if request.method == 'POST':
        category.name = request.POST.get('name', '').strip()
        if category.name:
            category.save()
            return redirect('category_detail', uuid=category.uuid)
        error_message = 'Nama kategori wajib diisi.'
    return render(
        request,
        'inventory/category_edit.html',
        {'category': category, 'error_message': error_message},
    )


@role_required('admin_toko', 'pembelian')
def category_delete(request, uuid):
    category = get_object_or_404(Category, uuid=uuid)
    if request.method == 'POST':
        category.delete()
    return redirect('category_list')
