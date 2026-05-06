from django.core.paginator import Paginator
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.db.models import Q
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.core.exceptions import ValidationError
from decimal import Decimal, InvalidOperation
import json
from datetime import date

from core.decorators import role_required
from .models import Category, DailyClosing, InventoryTransaction, Product, ProductPriceTier, Supplier, Unit
from .services import (
    close_daily,
    create_purchase_transaction,
    delete_purchase_transaction,
    edit_purchase_transaction,
    post_internal_used,
    post_purchase,
    post_stock_opname,
    low_stock_products,
)

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
    products = Product.objects.select_related('category', 'unit').prefetch_related('price_tiers').order_by('name')
    if query:
        products = products.filter(
            Q(name__icontains=query) |
            Q(sku__icontains=query) |
            Q(barcode__icontains=query) |
            Q(category__name__icontains=query) |
            Q(unit__name__icontains=query) |
            Q(unit__code__icontains=query)
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
    units = Unit.objects.filter(is_active=True).order_by('name')
    tier_rows = _default_tier_rows()
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        sku = request.POST.get('sku', '').strip()
        barcode = request.POST.get('barcode', '').strip()
        category_id = request.POST.get('category_id', '').strip()
        unit_id = request.POST.get('unit_id', '').strip()
        tier_rows = _extract_tier_rows(request)

        if not all([name, sku, category_id, unit_id]):
            error_message = 'Nama, SKU, kategori, dan satuan wajib diisi.'
            messages.error(request, error_message)
        else:
            try:
                category = Category.objects.get(id=category_id)
                unit = Unit.objects.get(id=unit_id, is_active=True)
                if barcode and Product.objects.filter(barcode=barcode).exists():
                    error_message = 'Barcode sudah dipakai.'
                    messages.error(request, error_message)
                    return render(
                        request,
                        'inventory/product_create.html',
                        {
                            'error_message': error_message,
                            'categories': categories,
                            'units': units,
                            'tier_rows': tier_rows,
                            'tier_rows_json': _to_json_payload(tier_rows),
                        },
                    )
                with transaction.atomic():
                    product = Product.objects.create(
                        name=name,
                        sku=sku,
                        barcode=barcode or None,
                        stock=0,
                        category=category,
                        unit=unit,
                    )
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
            except Unit.DoesNotExist:
                error_message = 'Satuan tidak valid atau nonaktif.'
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
            'units': units,
            'tier_rows': tier_rows,
            'tier_rows_json': _to_json_payload(tier_rows),
        },
    )


@role_required('admin_toko', 'kasir', 'pembelian')
def product_detail(request, uuid):
    product = get_object_or_404(
        Product.objects.select_related('category', 'unit').prefetch_related('price_tiers'),
        uuid=uuid,
    )
    return render(request, 'inventory/product_detail.html', {'product': product})


@role_required('admin_toko', 'pembelian')
def product_edit(request, uuid):
    product = get_object_or_404(Product.objects.prefetch_related('price_tiers'), uuid=uuid)
    categories = Category.objects.order_by('name')
    units = Unit.objects.filter(is_active=True).order_by('name')
    error_message = ''
    next_url = _get_safe_next_url(request)
    back_url = next_url or f"/inventory/products/{product.uuid}/"
    tier_rows = _serialize_tiers(product) or _default_tier_rows()

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        sku = request.POST.get('sku', '').strip()
        barcode = request.POST.get('barcode', '').strip()
        category_id = request.POST.get('category_id', '').strip()
        unit_id = request.POST.get('unit_id', '').strip()
        tier_rows = _extract_tier_rows(request)

        if not all([name, sku, category_id, unit_id]):
            error_message = 'Nama, SKU, kategori, dan satuan wajib diisi.'
            messages.error(request, error_message)
        else:
            try:
                category = Category.objects.get(id=category_id)
                unit = Unit.objects.get(id=unit_id, is_active=True)
                if Product.objects.exclude(id=product.id).filter(sku=sku).exists():
                    error_message = 'SKU sudah dipakai.'
                    messages.error(request, error_message)
                    return render(
                        request,
                        'inventory/product_edit.html',
                        {
                            'product': product,
                            'categories': categories,
                            'units': units,
                            'error_message': error_message,
                            'back_url': back_url,
                            'next_url': next_url,
                            'tier_rows': tier_rows,
                            'tier_rows_json': _to_json_payload(tier_rows),
                        },
                    )
                if barcode and Product.objects.exclude(id=product.id).filter(barcode=barcode).exists():
                    error_message = 'Barcode sudah dipakai.'
                    messages.error(request, error_message)
                    return render(
                        request,
                        'inventory/product_edit.html',
                        {
                            'product': product,
                            'categories': categories,
                            'units': units,
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
                    product.barcode = barcode or None
                    product.category = category
                    product.unit = unit
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
            except Unit.DoesNotExist:
                error_message = 'Satuan tidak valid atau nonaktif.'
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
            'units': units,
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
        try:
            product_name = product.name
            product.delete()
            messages.warning(request, f'Produk "{product_name}" berhasil dihapus.')
        except ProtectedError:
            messages.error(request, 'Produk tidak bisa dihapus karena sudah dipakai transaksi.')
    else:
        messages.info(request, 'Penghapusan dibatalkan.')
    return redirect('product_list')


@role_required('admin_toko', 'pembelian')
def purchase_page(request):
    query = request.GET.get('q', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    txs = InventoryTransaction.objects.select_related('supplier').filter(
        tx_type=InventoryTransaction.TYPE_PURCHASE
    ).order_by('-tx_date', '-created_at')
    if query:
        txs = txs.filter(Q(tx_number__icontains=query) | Q(supplier__name__icontains=query))
    if date_from:
        txs = txs.filter(tx_date__gte=date_from)
    if date_to:
        txs = txs.filter(tx_date__lte=date_to)
    close_dates = set(
        DailyClosing.objects.filter(
            is_locked=True,
            close_date__in=txs.values_list('tx_date', flat=True),
        ).values_list('close_date', flat=True)
    )
    page_obj = Paginator(txs, 10).get_page(request.GET.get('page'))
    for tx in page_obj:
        tx.can_modify = tx.tx_date not in close_dates
    return render(
        request,
        'inventory/purchase_list.html',
        {'page_obj': page_obj, 'query': query, 'date_from': date_from, 'date_to': date_to},
    )


def _extract_purchase_items(request):
    product_ids = request.POST.getlist('product_id[]')
    qtys = request.POST.getlist('qty[]')
    costs = request.POST.getlist('unit_cost[]')
    items = []
    size = max(len(product_ids), len(qtys), len(costs))
    for i in range(size):
        pid = product_ids[i].strip() if i < len(product_ids) else ''
        q = qtys[i].strip() if i < len(qtys) else ''
        c = costs[i].strip() if i < len(costs) else ''
        if not any([pid, q, c]):
            continue
        if not all([pid, q, c]):
            raise ValidationError('Setiap baris item pembelian wajib diisi lengkap.')
        product = Product.objects.get(id=pid)
        items.append({'product': product, 'qty': int(q), 'unit_cost': Decimal(c)})
    return items


@role_required('admin_toko', 'pembelian')
def purchase_create(request):
    products = Product.objects.select_related('unit').order_by('name')
    suppliers = Supplier.objects.filter(is_active=True).order_by('name')
    if request.method == 'POST':
        try:
            supplier = Supplier.objects.get(id=request.POST.get('supplier_id', '').strip())
            tx_date_raw = request.POST.get('tx_date', '').strip()
            tx_date = date.fromisoformat(tx_date_raw) if tx_date_raw else date.today()
            note = request.POST.get('note', '').strip()
            items = _extract_purchase_items(request)
            create_purchase_transaction(
                supplier=supplier,
                tx_date=tx_date,
                items=items,
                user=request.user,
                note=note,
            )
            messages.success(request, 'Transaksi pembelian berhasil disimpan.')
            return redirect('purchase_page')
        except Exception as exc:
            messages.error(request, str(exc))
    suppliers_data = [
        {
            'id': str(s.id),
            'code': s.code or '',
            'name': s.name,
            'phone': s.phone or '',
            'email': s.email or '',
            'address': s.address or '',
            'city': s.city or '',
        }
        for s in suppliers
    ]
    products_data = [
        {
            'id': str(p.id),
            'name': p.name,
            'sku': p.sku,
            'unit': p.unit.name if p.unit else '-',
        }
        for p in products
    ]
    return render(
        request,
        'inventory/purchase_create.html',
        {
            'products': products,
            'suppliers': suppliers,
            'suppliers_json': _to_json_payload(suppliers_data),
            'products_json': _to_json_payload(products_data),
        },
    )


@role_required('admin_toko', 'pembelian')
def purchase_detail(request, uuid):
    tx = get_object_or_404(
        InventoryTransaction.objects.select_related('supplier').prefetch_related('items__product'),
        uuid=uuid,
        tx_type=InventoryTransaction.TYPE_PURCHASE,
    )
    can_modify = not DailyClosing.objects.filter(close_date=tx.tx_date, is_locked=True).exists()
    return render(request, 'inventory/purchase_detail.html', {'tx': tx, 'can_modify': can_modify})


@role_required('admin_toko', 'pembelian')
def purchase_edit(request, uuid):
    tx = get_object_or_404(
        InventoryTransaction.objects.select_related('supplier').prefetch_related('items__product'),
        uuid=uuid,
        tx_type=InventoryTransaction.TYPE_PURCHASE,
    )
    if DailyClosing.objects.filter(close_date=tx.tx_date, is_locked=True).exists():
        messages.error(request, 'Transaksi pembelian ini tidak bisa diedit karena tanggalnya sudah tutup harian.')
        return redirect('purchase_detail', uuid=tx.uuid)
    products = Product.objects.select_related('unit').order_by('name')
    suppliers = Supplier.objects.filter(is_active=True).order_by('name')
    next_url = _get_safe_next_url(request)
    back_url = next_url or f'/inventory/purchases/{tx.uuid}/'
    if request.method == 'POST':
        try:
            supplier = Supplier.objects.get(id=request.POST.get('supplier_id', '').strip())
            tx_date_raw = request.POST.get('tx_date', '').strip()
            tx_date = date.fromisoformat(tx_date_raw) if tx_date_raw else tx.tx_date
            note = request.POST.get('note', '').strip()
            items = _extract_purchase_items(request)
            edit_purchase_transaction(
                tx=tx,
                supplier=supplier,
                tx_date=tx_date,
                items=items,
                user=request.user,
                note=note,
            )
            messages.success(request, 'Transaksi pembelian berhasil diperbarui.')
            return redirect('purchase_page')
        except Exception as exc:
            messages.error(request, str(exc))
    return render(
        request,
        'inventory/purchase_edit.html',
        {
            'tx': tx,
            'products': products,
            'suppliers': suppliers,
            'next_url': next_url,
            'back_url': back_url,
            'selected_supplier_id': str(tx.supplier_id) if tx.supplier_id else '',
            'suppliers_json': _to_json_payload(
                [
                    {
                        'id': str(s.id),
                        'code': s.code or '',
                        'name': s.name,
                        'phone': s.phone or '',
                        'email': s.email or '',
                        'address': s.address or '',
                        'city': s.city or '',
                    }
                    for s in suppliers
                ]
            ),
            'products_json': _to_json_payload(
                [
                    {
                        'id': str(p.id),
                        'name': p.name,
                        'sku': p.sku,
                        'unit': p.unit.name if p.unit else '-',
                    }
                    for p in products
                ]
            ),
            'initial_items_json': _to_json_payload(
                [
                    {
                        'product_id': str(it.product_id),
                        'qty': it.qty,
                        'unit_cost': str(it.unit_cost),
                    }
                    for it in tx.items.all()
                ]
            ),
        },
    )


@role_required('admin_toko', 'pembelian')
def purchase_delete(request, uuid):
    tx = get_object_or_404(
        InventoryTransaction.objects.select_related('supplier').prefetch_related('items__product'),
        uuid=uuid,
        tx_type=InventoryTransaction.TYPE_PURCHASE,
    )
    if DailyClosing.objects.filter(close_date=tx.tx_date, is_locked=True).exists():
        messages.error(request, 'Transaksi pembelian ini tidak bisa dihapus karena tanggalnya sudah tutup harian.')
        return redirect('purchase_detail', uuid=tx.uuid)
    if request.method == 'POST':
        try:
            delete_purchase_transaction(tx)
            messages.warning(request, 'Transaksi pembelian berhasil dihapus.')
        except Exception as exc:
            messages.error(request, str(exc))
    return redirect('purchase_page')


@role_required('admin_toko', 'pembelian')
def internal_used_page(request):
    products = Product.objects.order_by('name')
    if request.method == 'POST':
        try:
            product = Product.objects.get(id=request.POST.get('product_id'))
            qty = int(request.POST.get('qty', '0'))
            note = request.POST.get('note', '').strip()
            post_internal_used(product=product, qty=qty, user=request.user, note=note)
            messages.success(request, 'Transaksi internal used berhasil diposting.')
            return redirect('internal_used_page')
        except Exception as exc:
            messages.error(request, str(exc))
    return render(request, 'inventory/internal_used_page.html', {'products': products})


@role_required('admin_toko', 'pembelian')
def stock_opname_page(request):
    products = Product.objects.order_by('name')
    if request.method == 'POST':
        try:
            product = Product.objects.get(id=request.POST.get('product_id'))
            actual_stock = int(request.POST.get('actual_stock', '0'))
            note = request.POST.get('note', '').strip()
            post_stock_opname(product=product, actual_stock=actual_stock, user=request.user, note=note)
            messages.success(request, 'Transaksi stock opname berhasil diposting.')
            return redirect('stock_opname_page')
        except Exception as exc:
            messages.error(request, str(exc))
    return render(request, 'inventory/stock_opname_page.html', {'products': products})


@role_required('admin_toko')
def daily_closing_page(request):
    if request.method == 'POST':
        try:
            close_date_raw = request.POST.get('close_date', '')
            close_date = date.fromisoformat(close_date_raw) if close_date_raw else date.today()
            note = request.POST.get('note', '').strip()
            close_daily(closing_date=close_date, user=request.user, note=note)
            messages.success(request, f'Tutup harian {close_date} berhasil.')
            return redirect('daily_closing_page')
        except Exception as exc:
            messages.error(request, str(exc))
    return render(request, 'inventory/daily_closing_page.html')


@role_required('admin_toko', 'pembelian', 'kasir')
def stock_card_report(request):
    products = Product.objects.order_by('name')
    product_id = request.GET.get('product_id', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    ledgers = []
    selected_product = None
    if product_id:
        selected_product = Product.objects.filter(id=product_id).first()
        if selected_product:
            ledgers = selected_product.stock_ledgers.select_related('tx').order_by('tx_date', 'created_at')
            if date_from:
                ledgers = ledgers.filter(tx_date__gte=date_from)
            if date_to:
                ledgers = ledgers.filter(tx_date__lte=date_to)
    return render(
        request,
        'inventory/stock_card_report.html',
        {
            'products': products,
            'selected_product': selected_product,
            'ledgers': ledgers,
            'date_from': date_from,
            'date_to': date_to,
        },
    )


@role_required('admin_toko', 'pembelian')
def reorder_alert_page(request):
    return render(request, 'inventory/reorder_alert_page.html', {'products': low_stock_products()})


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
        try:
            category_name = category.name
            category.delete()
            messages.warning(request, f'Kategori "{category_name}" berhasil dihapus.')
        except ProtectedError:
            messages.error(request, 'Kategori tidak bisa dihapus karena sudah dipakai produk/transaksi.')
    else:
        messages.info(request, 'Penghapusan dibatalkan.')
    return redirect('category_list')


@role_required('admin_toko', 'pembelian')
def unit_list(request):
    query = request.GET.get('q', '').strip()
    units = Unit.objects.order_by('name')
    if query:
        units = units.filter(Q(name__icontains=query) | Q(code__icontains=query))

    page_obj = Paginator(units, 10).get_page(request.GET.get('page'))
    return render(request, 'inventory/unit_list.html', {'page_obj': page_obj, 'query': query})


@role_required('admin_toko', 'pembelian')
def unit_create(request):
    error_message = ''
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip().upper()
        description = request.POST.get('description', '').strip()
        is_active = request.POST.get('is_active') == 'on'
        if not name or not code:
            error_message = 'Nama dan kode satuan wajib diisi.'
            messages.error(request, error_message)
        elif Unit.objects.filter(code__iexact=code).exists():
            error_message = 'Kode satuan sudah dipakai.'
            messages.error(request, error_message)
        else:
            Unit.objects.create(name=name, code=code, description=description, is_active=is_active)
            messages.success(request, 'Satuan berhasil ditambahkan.')
            return redirect('unit_list')
    return render(request, 'inventory/unit_create.html', {'error_message': error_message})


@role_required('admin_toko', 'pembelian')
def unit_detail(request, uuid):
    unit = get_object_or_404(Unit, uuid=uuid)
    return render(request, 'inventory/unit_detail.html', {'unit': unit})


@role_required('admin_toko', 'pembelian')
def unit_edit(request, uuid):
    unit = get_object_or_404(Unit, uuid=uuid)
    next_url = _get_safe_next_url(request)
    back_url = next_url or f'/inventory/units/{unit.uuid}/'
    error_message = ''
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip().upper()
        description = request.POST.get('description', '').strip()
        is_active = request.POST.get('is_active') == 'on'
        if not name or not code:
            error_message = 'Nama dan kode satuan wajib diisi.'
            messages.error(request, error_message)
        elif Unit.objects.exclude(id=unit.id).filter(code__iexact=code).exists():
            error_message = 'Kode satuan sudah dipakai.'
            messages.error(request, error_message)
        else:
            unit.name = name
            unit.code = code
            unit.description = description
            unit.is_active = is_active
            unit.save()
            messages.success(request, 'Satuan berhasil diperbarui.')
            return redirect('unit_list')
    return render(
        request,
        'inventory/unit_edit.html',
        {'unit': unit, 'next_url': next_url, 'back_url': back_url, 'error_message': error_message},
    )


@role_required('admin_toko', 'pembelian')
def unit_delete(request, uuid):
    unit = get_object_or_404(Unit, uuid=uuid)
    if request.method == 'POST':
        try:
            name = unit.name
            unit.delete()
            messages.warning(request, f'Satuan "{name}" berhasil dihapus.')
        except Exception:
            messages.error(request, 'Satuan tidak bisa dihapus karena sudah dipakai produk.')
    return redirect('unit_list')


@role_required('admin_toko', 'pembelian')
def supplier_list(request):
    query = request.GET.get('q', '').strip()
    suppliers = Supplier.objects.order_by('name')
    if query:
        suppliers = suppliers.filter(
            Q(code__icontains=query)
            | Q(name__icontains=query)
            | Q(contact_name__icontains=query)
            | Q(phone__icontains=query)
            | Q(email__icontains=query)
            | Q(city__icontains=query)
        )
    page_obj = Paginator(suppliers, 10).get_page(request.GET.get('page'))
    return render(request, 'inventory/supplier_list.html', {'page_obj': page_obj, 'query': query})


@role_required('admin_toko', 'pembelian')
def supplier_create(request):
    error_message = ''
    if request.method == 'POST':
        code = request.POST.get('code', '').strip().upper()
        name = request.POST.get('name', '').strip()
        contact_name = request.POST.get('contact_name', '').strip()
        phone = request.POST.get('phone', '').strip()
        email = request.POST.get('email', '').strip()
        address = request.POST.get('address', '').strip()
        city = request.POST.get('city', '').strip()
        is_active = request.POST.get('is_active') == 'on'
        if not code or not name:
            error_message = 'Kode dan nama supplier wajib diisi.'
            messages.error(request, error_message)
        elif Supplier.objects.filter(code__iexact=code).exists():
            error_message = 'Kode supplier sudah dipakai.'
            messages.error(request, error_message)
        elif Supplier.objects.filter(name__iexact=name).exists():
            error_message = 'Nama supplier sudah dipakai.'
            messages.error(request, error_message)
        else:
            Supplier.objects.create(
                code=code,
                name=name,
                contact_name=contact_name,
                phone=phone,
                email=email,
                address=address,
                city=city,
                is_active=is_active,
                created_by=request.user,
                updated_by=request.user,
            )
            messages.success(request, 'Supplier berhasil ditambahkan.')
            return redirect('supplier_list')
    return render(request, 'inventory/supplier_create.html', {'error_message': error_message})


@role_required('admin_toko', 'pembelian')
def supplier_detail(request, uuid):
    supplier = get_object_or_404(Supplier, uuid=uuid)
    return render(request, 'inventory/supplier_detail.html', {'supplier': supplier})


@role_required('admin_toko', 'pembelian')
def supplier_edit(request, uuid):
    supplier = get_object_or_404(Supplier, uuid=uuid)
    next_url = _get_safe_next_url(request)
    back_url = next_url or f'/inventory/suppliers/{supplier.uuid}/'
    error_message = ''
    if request.method == 'POST':
        code = request.POST.get('code', '').strip().upper()
        name = request.POST.get('name', '').strip()
        contact_name = request.POST.get('contact_name', '').strip()
        phone = request.POST.get('phone', '').strip()
        email = request.POST.get('email', '').strip()
        address = request.POST.get('address', '').strip()
        city = request.POST.get('city', '').strip()
        is_active = request.POST.get('is_active') == 'on'
        if not code or not name:
            error_message = 'Kode dan nama supplier wajib diisi.'
            messages.error(request, error_message)
        elif Supplier.objects.exclude(id=supplier.id).filter(code__iexact=code).exists():
            error_message = 'Kode supplier sudah dipakai.'
            messages.error(request, error_message)
        elif Supplier.objects.exclude(id=supplier.id).filter(name__iexact=name).exists():
            error_message = 'Nama supplier sudah dipakai.'
            messages.error(request, error_message)
        else:
            supplier.code = code
            supplier.name = name
            supplier.contact_name = contact_name
            supplier.phone = phone
            supplier.email = email
            supplier.address = address
            supplier.city = city
            supplier.is_active = is_active
            supplier.updated_by = request.user
            supplier.save()
            messages.success(request, 'Supplier berhasil diperbarui.')
            return redirect('supplier_list')
    return render(
        request,
        'inventory/supplier_edit.html',
        {'supplier': supplier, 'next_url': next_url, 'back_url': back_url, 'error_message': error_message},
    )


@role_required('admin_toko', 'pembelian')
def supplier_delete(request, uuid):
    supplier = get_object_or_404(Supplier, uuid=uuid)
    if request.method == 'POST':
        try:
            supplier_name = supplier.name
            supplier.delete()
            messages.warning(request, f'Supplier "{supplier_name}" berhasil dihapus.')
        except Exception:
            messages.error(request, 'Supplier tidak bisa dihapus karena sudah dipakai transaksi.')
    return redirect('supplier_list')
