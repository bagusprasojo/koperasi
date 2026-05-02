from django.core.paginator import Paginator
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.db.models import Q
from django.db import IntegrityError, transaction
from django.core.exceptions import ValidationError
from decimal import Decimal, InvalidOperation
import json

from core.decorators import role_required
from .models import Category, Product, ProductPriceTier

TWOPLACES = Decimal('0.01')


def _to_json_payload(value):
    return json.dumps(value, default=str)


def _get_safe_next_url(request):
    next_url = request.GET.get('next') or request.POST.get('next') or ''
    if not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return ''
    return next_url


def _extract_tier_rows(request):
    rows = []
    for level in [1, 2, 3]:
        rows.append(
            {
                'level': str(level),
                'min_qty': request.POST.get(f'tier_{level}_min_qty', '').strip(),
                'max_qty': request.POST.get(f'tier_{level}_max_qty', '').strip(),
                'mode': request.POST.get(f'tier_{level}_mode', 'final').strip(),
                'discount_type': request.POST.get(f'tier_{level}_discount_type', 'percent').strip(),
                'input_value': request.POST.get(f'tier_{level}_value', '').strip(),
                'price': '',
            }
        )
    return rows


def _serialize_tiers(product):
    by_level = {t.level: t for t in product.price_tiers.order_by('level')}
    rows = []
    for level in [1, 2, 3]:
        tier = by_level.get(level)
        if tier:
            rows.append(
                {
                    'level': str(level),
                    'min_qty': str(tier.min_qty),
                    'max_qty': str(tier.max_qty),
                    'mode': tier.source_mode or 'final',
                    'discount_type': tier.discount_type or 'percent',
                    'input_value': str(tier.discount_value if tier.source_mode == 'discount' and tier.discount_value is not None else tier.price),
                    'price': str(tier.price),
                }
            )
        else:
            rows.append(
                {
                    'level': str(level),
                    'min_qty': '',
                    'max_qty': '',
                    'mode': 'final',
                    'discount_type': 'percent',
                    'input_value': '',
                    'price': '',
                }
            )
    return rows


def _validate_contiguous_ranges(rows):
    prev_max = None
    for idx, row in enumerate(rows):
        min_qty = int(row['min_qty'])
        max_qty = int(row['max_qty'])
        if min_qty > max_qty:
            raise ValidationError(f'Level {idx + 1}: min qty tidak boleh lebih besar dari max qty.')
        if idx == 0 and min_qty != 1:
            raise ValidationError('Level 1 harus dimulai dari qty 1.')
        if prev_max is not None and min_qty != (prev_max + 1):
            raise ValidationError(
                f'Range level {idx} ke level {idx + 1} harus berurutan tanpa gap/overlap.'
            )
        prev_max = max_qty


def _compute_level_prices(rows):
    for row in rows:
        if not all([row['min_qty'], row['max_qty'], row['input_value']]):
            raise ValidationError('Semua field range dan nilai harga level 1-3 wajib diisi.')

    _validate_contiguous_ranges(rows)

    base_price = Decimal(rows[0]['input_value']).quantize(TWOPLACES)
    if base_price <= 0:
        raise ValidationError('Level 1 wajib memiliki harga lebih besar dari 0.')
    rows[0]['price'] = base_price
    rows[0]['source_mode'] = 'final'
    rows[0]['discount_type'] = ''
    rows[0]['discount_value'] = None

    for i in [1, 2]:
        mode = rows[i]['mode']
        value = Decimal(rows[i]['input_value']).quantize(TWOPLACES)
        if mode == 'final':
            price = value
            rows[i]['source_mode'] = 'final'
            rows[i]['discount_type'] = ''
            rows[i]['discount_value'] = None
        else:
            if rows[i]['discount_type'] == 'percent':
                if value < 0 or value > 100:
                    raise ValidationError(f'Level {i + 1}: diskon persen harus 0 sampai 100.')
                price = (base_price - ((value / Decimal('100')) * base_price)).quantize(TWOPLACES)
                rows[i]['source_mode'] = 'discount'
                rows[i]['discount_type'] = 'percent'
                rows[i]['discount_value'] = value
            else:
                if value < 0:
                    raise ValidationError(f'Level {i + 1}: diskon nominal tidak boleh negatif.')
                price = (base_price - value).quantize(TWOPLACES)
                rows[i]['source_mode'] = 'discount'
                rows[i]['discount_type'] = 'nominal'
                rows[i]['discount_value'] = value
        if price <= 0:
            raise ValidationError(f'Level {i + 1}: harga akhir harus lebih besar dari 0.')
        rows[i]['price'] = price.quantize(TWOPLACES)

    if not (rows[0]['price'] >= rows[1]['price'] >= rows[2]['price']):
        raise ValidationError('Harga level harus menurun atau sama: Level 1 >= Level 2 >= Level 3.')

    return rows


def _create_price_tiers(product, tier_rows):
    if len(tier_rows) != 3:
        raise ValidationError('Level harga harus tepat 3 level.')
    computed_rows = _compute_level_prices(tier_rows)
    for row in computed_rows:
        ProductPriceTier.objects.create(
            product=product,
            level=int(row['level']),
            min_qty=int(row['min_qty']),
            max_qty=int(row['max_qty']),
            price=Decimal(row['price']),
            source_mode=row['source_mode'],
            discount_type=row['discount_type'],
            discount_value=row['discount_value'],
        )


def _default_tier_rows():
    return [
        {
            'level': '1',
            'min_qty': '1',
            'max_qty': '',
            'mode': 'final',
            'discount_type': 'percent',
            'input_value': '',
            'price': '',
        },
        {
            'level': '2',
            'min_qty': '',
            'max_qty': '',
            'mode': 'discount',
            'discount_type': 'percent',
            'input_value': '',
            'price': '',
        },
        {
            'level': '3',
            'min_qty': '',
            'max_qty': '',
            'mode': 'discount',
            'discount_type': 'percent',
            'input_value': '',
            'price': '',
        }
    ]


@role_required('admin_toko', 'kasir', 'pembelian')
def product_list(request):
    query = request.GET.get('q', '').strip()
    products = Product.objects.select_related('category').prefetch_related('price_tiers').order_by('name')
    if query:
        products = products.filter(
            Q(name__icontains=query) |
            Q(sku__icontains=query) |
            Q(category__name__icontains=query)
        )

    paginator = Paginator(products, 10)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(
        request,
        'inventory/product_list.html',
        {'page_obj': page_obj, 'query': query},
    )


@role_required('admin_toko', 'pembelian')
def product_create(request):
    error_message = ''
    categories = Category.objects.order_by('name')
    tier_rows = _default_tier_rows()
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        sku = request.POST.get('sku', '').strip()
        category_id = request.POST.get('category_id', '').strip()
        tier_rows = _extract_tier_rows(request)

        if not all([name, sku, category_id]):
            error_message = 'Nama, SKU, dan kategori wajib diisi.'
            messages.error(request, error_message)
        else:
            try:
                category = Category.objects.get(id=category_id)
                with transaction.atomic():
                    product = Product.objects.create(name=name, sku=sku, stock=0, category=category)
                    _create_price_tiers(product, tier_rows)
                messages.success(request, 'Produk berhasil ditambahkan.')
                return redirect('product_list')
            except (ValueError, InvalidOperation):
                error_message = 'Format level harga tidak valid.'
                messages.error(request, error_message)
            except ValidationError as exc:
                error_message = exc.messages[0] if exc.messages else str(exc)
                messages.error(request, error_message)
            except Category.DoesNotExist:
                error_message = 'Kategori tidak valid.'
                messages.error(request, error_message)
            except IntegrityError:
                error_message = 'SKU sudah dipakai atau data tidak valid.'
                messages.error(request, error_message)

    return render(
        request,
        'inventory/product_create.html',
        {
            'error_message': error_message,
            'categories': categories,
            'tier_rows': tier_rows,
            'tier_rows_json': _to_json_payload(tier_rows),
        },
    )


@role_required('admin_toko', 'kasir', 'pembelian')
def product_detail(request, uuid):
    product = get_object_or_404(
        Product.objects.select_related('category').prefetch_related('price_tiers'),
        uuid=uuid,
    )
    return render(request, 'inventory/product_detail.html', {'product': product})


@role_required('admin_toko', 'pembelian')
def product_edit(request, uuid):
    product = get_object_or_404(Product.objects.prefetch_related('price_tiers'), uuid=uuid)
    categories = Category.objects.order_by('name')
    error_message = ''
    next_url = _get_safe_next_url(request)
    back_url = next_url or f"/inventory/products/{product.uuid}/"
    tier_rows = _serialize_tiers(product) or _default_tier_rows()

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        sku = request.POST.get('sku', '').strip()
        category_id = request.POST.get('category_id', '').strip()
        tier_rows = _extract_tier_rows(request)

        if not all([name, sku, category_id]):
            error_message = 'Nama, SKU, dan kategori wajib diisi.'
            messages.error(request, error_message)
        else:
            try:
                category = Category.objects.get(id=category_id)
                if Product.objects.exclude(id=product.id).filter(sku=sku).exists():
                    error_message = 'SKU sudah dipakai.'
                    messages.error(request, error_message)
                    return render(
                        request,
                        'inventory/product_edit.html',
                        {
                            'product': product,
                            'categories': categories,
                            'error_message': error_message,
                            'back_url': back_url,
                            'next_url': next_url,
                            'tier_rows': tier_rows,
                            'tier_rows_json': _to_json_payload(tier_rows),
                        },
                    )
                with transaction.atomic():
                    product.name = name
                    product.sku = sku
                    product.category = category
                    product.save()
                    product.price_tiers.all().delete()
                    _create_price_tiers(product, tier_rows)
                messages.success(request, 'Produk berhasil diperbarui.')
                return redirect('product_list')
            except (ValueError, InvalidOperation):
                error_message = 'Format level harga tidak valid.'
                messages.error(request, error_message)
            except ValidationError as exc:
                error_message = exc.messages[0] if exc.messages else str(exc)
                messages.error(request, error_message)
            except Category.DoesNotExist:
                error_message = 'Kategori tidak valid.'
                messages.error(request, error_message)
            except IntegrityError:
                error_message = 'SKU sudah dipakai atau data tidak valid.'
                messages.error(request, error_message)

    return render(
        request,
        'inventory/product_edit.html',
        {
            'product': product,
            'categories': categories,
            'error_message': error_message,
            'back_url': back_url,
            'next_url': next_url,
            'tier_rows': tier_rows,
            'tier_rows_json': _to_json_payload(tier_rows),
        },
    )


@role_required('admin_toko', 'pembelian')
def product_delete(request, uuid):
    product = get_object_or_404(Product, uuid=uuid)
    if request.method == 'POST':
        product_name = product.name
        product.delete()
        messages.warning(request, f'Produk "{product_name}" berhasil dihapus.')
    else:
        messages.info(request, 'Penghapusan dibatalkan.')
    return redirect('product_list')


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
def category_create(request):
    error_message = ''
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if name:
            Category.objects.create(name=name)
            messages.success(request, 'Kategori berhasil ditambahkan.')
            return redirect('category_list')
        error_message = 'Nama kategori wajib diisi.'
        messages.error(request, error_message)
    return render(
        request,
        'inventory/category_create.html',
        {'error_message': error_message},
    )


@role_required('admin_toko', 'pembelian')
def category_detail(request, uuid):
    category = get_object_or_404(Category, uuid=uuid)
    return render(request, 'inventory/category_detail.html', {'category': category})


@role_required('admin_toko', 'pembelian')
def category_edit(request, uuid):
    category = get_object_or_404(Category, uuid=uuid)
    error_message = ''
    next_url = _get_safe_next_url(request)
    back_url = next_url or f"/inventory/categories/{category.uuid}/"

    if request.method == 'POST':
        category.name = request.POST.get('name', '').strip()
        if category.name:
            category.save()
            messages.success(request, 'Kategori berhasil diperbarui.')
            return redirect('category_list')
        error_message = 'Nama kategori wajib diisi.'
        messages.error(request, error_message)
    return render(
        request,
        'inventory/category_edit.html',
        {'category': category, 'error_message': error_message, 'back_url': back_url, 'next_url': next_url},
    )


@role_required('admin_toko', 'pembelian')
def category_delete(request, uuid):
    category = get_object_or_404(Category, uuid=uuid)
    if request.method == 'POST':
        category_name = category.name
        category.delete()
        messages.warning(request, f'Kategori "{category_name}" berhasil dihapus.')
    else:
        messages.info(request, 'Penghapusan dibatalkan.')
    return redirect('category_list')
