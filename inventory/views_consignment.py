from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.constants import Role, STAFF_ROLES, MANAGEMENT_ROLES
from core.decorators import role_required
from members.models import Member

from .consignment_services import (
    cancel_consignment_batch,
    create_consignment_product,
    create_or_update_consignor,
    generate_consignor_code,
    get_consignment_settlement_preview,
    record_consignment_inflow,
    settle_consignment_batch,
)
from .models import (
    Category,
    ConsignmentBatch,
    ConsignmentBatchItem,
    Consignor,
    Product,
    Unit,
)


def _exc_msg(exc):
    if isinstance(exc, ValidationError):
        return exc.messages[0] if exc.messages else str(exc)
    return str(exc)


@role_required(*STAFF_ROLES, perm='manage_consignments')
def consignor_list(request):
    query = (request.GET.get('q') or '').strip()
    status_filter = (request.GET.get('status') or 'active').strip()

    qs = Consignor.objects.select_related('member').annotate(
        product_count=Count('products', distinct=True),
        batch_count=Count('batches', distinct=True),
    ).order_by('name')

    if query:
        qs = qs.filter(
            Q(name__icontains=query) |
            Q(code__icontains=query) |
            Q(phone__icontains=query) |
            Q(member__full_name__icontains=query)
        )

    if status_filter == 'active':
        qs = qs.filter(is_active=True)
    elif status_filter == 'inactive':
        qs = qs.filter(is_active=False)

    total_consignors = Consignor.objects.count()
    active_consignors = Consignor.objects.filter(is_active=True).count()
    total_consignment_products = Product.objects.filter(is_consignment=True).count()
    today_active_batches = ConsignmentBatch.objects.filter(
        batch_date=date.today(),
        status=ConsignmentBatch.STATUS_OPEN
    ).count()

    paginator = Paginator(qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(
        request,
        'inventory/consignor_list.html',
        {
            'page_obj': page_obj,
            'query': query,
            'status_filter': status_filter,
            'total_consignors': total_consignors,
            'active_consignors': active_consignors,
            'total_consignment_products': total_consignment_products,
            'today_active_batches': today_active_batches,
        },
    )


@role_required(*STAFF_ROLES, perm='manage_consignments')
def consignor_create(request):
    members = Member.objects.filter(is_active=True).order_by('full_name')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip()
        phone = request.POST.get('phone', '').strip()
        address = request.POST.get('address', '').strip()
        member_id = request.POST.get('member_id') or None
        bank_name = request.POST.get('bank_name', '').strip()
        bank_account = request.POST.get('bank_account', '').strip()
        bank_account_holder = request.POST.get('bank_account_holder', '').strip()
        notes = request.POST.get('notes', '').strip()
        is_active = request.POST.get('is_active') == '1'

        try:
            consignor = create_or_update_consignor(
                code=code,
                name=name,
                phone=phone,
                address=address,
                member_id=member_id,
                bank_name=bank_name,
                bank_account=bank_account,
                bank_account_holder=bank_account_holder,
                is_active=is_active,
                notes=notes,
                user=request.user,
            )
            messages.success(request, f"Penitip '{consignor.name}' ({consignor.code}) berhasil didaftarkan.")
            return redirect('consignor_detail', uuid=consignor.uuid)
        except Exception as exc:
            messages.error(request, _exc_msg(exc))

    auto_code = generate_consignor_code()
    return render(
        request,
        'inventory/consignor_form.html',
        {
            'is_edit': False,
            'auto_code': auto_code,
            'members': members,
        },
    )


@role_required(*STAFF_ROLES, perm='manage_consignments')
def consignor_edit(request, uuid):
    consignor = get_object_or_404(Consignor, uuid=uuid)
    members = Member.objects.filter(is_active=True).order_by('full_name')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip()
        phone = request.POST.get('phone', '').strip()
        address = request.POST.get('address', '').strip()
        member_id = request.POST.get('member_id') or None
        bank_name = request.POST.get('bank_name', '').strip()
        bank_account = request.POST.get('bank_account', '').strip()
        bank_account_holder = request.POST.get('bank_account_holder', '').strip()
        notes = request.POST.get('notes', '').strip()
        is_active = request.POST.get('is_active') == '1'

        try:
            consignor = create_or_update_consignor(
                consignor_id=consignor.id,
                code=code,
                name=name,
                phone=phone,
                address=address,
                member_id=member_id,
                bank_name=bank_name,
                bank_account=bank_account,
                bank_account_holder=bank_account_holder,
                is_active=is_active,
                notes=notes,
                user=request.user,
            )
            messages.success(request, f"Data penitip '{consignor.name}' berhasil diperbarui.")
            return redirect('consignor_detail', uuid=consignor.uuid)
        except Exception as exc:
            messages.error(request, _exc_msg(exc))

    return render(
        request,
        'inventory/consignor_form.html',
        {
            'is_edit': True,
            'consignor': consignor,
            'members': members,
        },
    )


@role_required(*STAFF_ROLES, perm='manage_consignments')
def consignor_detail(request, uuid):
    consignor = get_object_or_404(Consignor.objects.select_related('member'), uuid=uuid)
    products = consignor.products.select_related('unit', 'category').prefetch_related('price_tiers').all()
    batches = consignor.batches.select_related('settled_by', 'created_by').order_by('-batch_date', '-created_at')[:20]

    categories = Category.objects.all().order_by('name')
    units = Unit.objects.filter(is_active=True).order_by('name')

    total_settled_batches = consignor.batches.filter(status=ConsignmentBatch.STATUS_SETTLED).count()
    total_paid_out = consignor.batches.filter(status=ConsignmentBatch.STATUS_SETTLED).aggregate(total=Sum('total_sold_cost'))['total'] or Decimal('0.00')
    total_margin_earned = consignor.batches.filter(status=ConsignmentBatch.STATUS_SETTLED).aggregate(total=Sum('total_coop_margin'))['total'] or Decimal('0.00')

    return render(
        request,
        'inventory/consignor_detail.html',
        {
            'consignor': consignor,
            'products': products,
            'batches': batches,
            'categories': categories,
            'units': units,
            'total_settled_batches': total_settled_batches,
            'total_paid_out': total_paid_out,
            'total_margin_earned': total_margin_earned,
        },
    )


@role_required(*STAFF_ROLES, perm='manage_consignments')
@require_POST
def consignor_product_create(request, uuid):
    consignor = get_object_or_404(Consignor, uuid=uuid)

    name = request.POST.get('name', '').strip()
    category_id = request.POST.get('category_id')
    unit_id = request.POST.get('unit_id') or None
    sku = request.POST.get('sku', '').strip()
    barcode = request.POST.get('barcode', '').strip()
    cost_price = request.POST.get('cost_price', '0')
    sale_price = request.POST.get('sale_price', '0')
    allow_decimal_qty = request.POST.get('allow_decimal_qty') == '1'

    try:
        product = create_consignment_product(
            consignor=consignor,
            name=name,
            category_id=category_id,
            unit_id=unit_id,
            cost_price=Decimal(cost_price),
            sale_price=Decimal(sale_price),
            sku=sku,
            barcode=barcode,
            allow_decimal_qty=allow_decimal_qty,
            user=request.user,
        )
        messages.success(request, f"Produk titipan '{product.name}' ({product.sku}) berhasil didaftarkan!")
    except Exception as exc:
        messages.error(request, _exc_msg(exc))

    return redirect('consignor_detail', uuid=consignor.uuid)


@role_required(*STAFF_ROLES, perm='manage_consignments')
@require_GET
def consignor_products_json(request, consignor_id):
    consignor = get_object_or_404(Consignor, id=consignor_id)
    products = consignor.products.select_related('unit').prefetch_related('price_tiers').all()

    data = []
    for p in products:
        tier1 = p.price_tiers.filter(level=1).first()
        sale_price = tier1.price if tier1 else p.cost_of_goods_sold
        data.append({
            'id': p.id,
            'name': p.name,
            'sku': p.sku,
            'barcode': p.barcode or '',
            'unit': p.unit.name if p.unit else 'pcs',
            'stock': float(p.stock),
            'cost_price': float(p.cost_of_goods_sold),
            'sale_price': float(sale_price),
            'allow_decimal_qty': p.allow_decimal_qty,
        })
    return JsonResponse({'success': True, 'products': data})


@role_required(*STAFF_ROLES, perm='manage_consignments')
@require_POST
def consignor_product_create_api(request, consignor_id):
    consignor = get_object_or_404(Consignor, id=consignor_id)
    name = request.POST.get('name', '').strip()
    category_id = request.POST.get('category_id')
    unit_id = request.POST.get('unit_id') or None
    cost_price = request.POST.get('cost_price', '0')
    sale_price = request.POST.get('sale_price', '0')
    allow_decimal_qty = request.POST.get('allow_decimal_qty') == '1'

    try:
        product = create_consignment_product(
            consignor=consignor,
            name=name,
            category_id=category_id,
            unit_id=unit_id,
            cost_price=Decimal(cost_price),
            sale_price=Decimal(sale_price),
            allow_decimal_qty=allow_decimal_qty,
            user=request.user,
        )
        return JsonResponse({
            'success': True,
            'product': {
                'id': product.id,
                'name': product.name,
                'sku': product.sku,
                'cost_price': float(product.cost_of_goods_sold),
                'sale_price': float(Decimal(sale_price)),
                'unit': product.unit.name if product.unit else 'pcs',
                'stock': float(product.stock),
                'allow_decimal_qty': product.allow_decimal_qty,
            }
        })
    except Exception as exc:
        return JsonResponse({'success': False, 'message': _exc_msg(exc)}, status=400)


@role_required(*STAFF_ROLES, perm='manage_consignments')
def consignment_inflow(request):
    """
    Penerimaan barang titipan di pagi hari.
    """
    consignors = Consignor.objects.filter(is_active=True).order_by('name')
    categories = Category.objects.all().order_by('name')
    units = Unit.objects.filter(is_active=True).order_by('name')

    selected_consignor_id = request.GET.get('consignor_id') or ''

    if request.method == 'POST':
        consignor_id = request.POST.get('consignor_id')
        batch_date_raw = request.POST.get('batch_date') or str(date.today())
        notes = request.POST.get('notes', '').strip()
        items_json = request.POST.get('items_json', '[]')

        try:
            batch_date = timezone.datetime.fromisoformat(batch_date_raw).date()
            consignor = get_object_or_404(Consignor, id=consignor_id)
            parsed_items = json.loads(items_json)

            if not parsed_items:
                raise ValidationError('Daftar barang titipan tidak boleh kosong.')

            items_data = []
            for row in parsed_items:
                product_id = row.get('product_id')
                product = Product.objects.filter(id=product_id, consignor=consignor).first()
                if not product:
                    raise ValidationError(f"Produk titipan ID {product_id} tidak valid untuk penitip {consignor.name}.")
                items_data.append({
                    'product': product,
                    'qty': row.get('qty', 0),
                    'cost_price': row.get('cost_price', product.cost_of_goods_sold),
                    'sale_price': row.get('sale_price', 0),
                    'notes': row.get('notes', ''),
                })

            batch = record_consignment_inflow(
                consignor=consignor,
                batch_date=batch_date,
                items_data=items_data,
                user=request.user,
                notes=notes,
            )
            messages.success(
                request,
                f"Penerimaan barang titipan pagi dari '{consignor.name}' berhasil dicatat! "
                f"No. Batch: {batch.batch_number}"
            )
            return redirect('consignment_batch_receipt', uuid=batch.uuid)
        except Exception as exc:
            messages.error(request, _exc_msg(exc))

    return render(
        request,
        'inventory/consignment_inflow.html',
        {
            'consignors': consignors,
            'categories': categories,
            'units': units,
            'today': date.today(),
            'selected_consignor_id': selected_consignor_id,
        },
    )


@role_required(*STAFF_ROLES, perm='manage_consignments')
def consignment_batch_receipt(request, uuid):
    batch = get_object_or_404(
        ConsignmentBatch.objects.select_related('consignor', 'consignor__member', 'created_by'),
        uuid=uuid,
    )
    items = batch.items.select_related('product', 'product__unit').all()

    return render(
        request,
        'inventory/consignment_receipt.html',
        {
            'batch': batch,
            'items': items,
        },
    )


@role_required(*STAFF_ROLES, perm='manage_consignments')
def consignment_settlement_list(request):
    """
    Daftar batch titipan untuk rekapitulasi sore hari.
    """
    status_filter = (request.GET.get('status') or 'open').strip()
    query = (request.GET.get('q') or '').strip()
    date_filter = (request.GET.get('date') or '').strip()

    qs = ConsignmentBatch.objects.select_related('consignor', 'created_by', 'settled_by').order_by('-batch_date', '-created_at')

    if query:
        qs = qs.filter(
            Q(batch_number__icontains=query) |
            Q(consignor__name__icontains=query) |
            Q(consignor__code__icontains=query)
        )

    if status_filter in ['open', 'settled', 'cancelled']:
        qs = qs.filter(status=status_filter)

    if date_filter:
        try:
            parsed_date = timezone.datetime.fromisoformat(date_filter).date()
            qs = qs.filter(batch_date=parsed_date)
        except ValueError:
            pass

    open_count = ConsignmentBatch.objects.filter(status=ConsignmentBatch.STATUS_OPEN).count()
    settled_today_count = ConsignmentBatch.objects.filter(
        status=ConsignmentBatch.STATUS_SETTLED,
        batch_date=date.today(),
    ).count()

    paginator = Paginator(qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(
        request,
        'inventory/consignment_settlement_list.html',
        {
            'page_obj': page_obj,
            'status_filter': status_filter,
            'query': query,
            'date_filter': date_filter,
            'open_count': open_count,
            'settled_today_count': settled_today_count,
            'today': date.today(),
        },
    )


@role_required(*STAFF_ROLES, perm='manage_consignments')
def consignment_settle_detail(request, uuid):
    """
    Halaman rekapitulasi sore dan pelunasan pembayaran penitip.
    """
    batch = get_object_or_404(
        ConsignmentBatch.objects.select_related('consignor', 'consignor__member', 'created_by'),
        uuid=uuid,
    )

    if request.method == 'POST':
        if batch.status != ConsignmentBatch.STATUS_OPEN:
            messages.error(request, 'Batch titipan ini sudah diselesaikan sebelumnya.')
            return redirect('consignment_settle_detail', uuid=batch.uuid)

        payout_method = request.POST.get('payout_method', '').strip()
        payout_reference = request.POST.get('payout_reference', '').strip()
        notes = request.POST.get('notes', '').strip()
        settlement_items_json = request.POST.get('settlement_items_json', '[]')

        try:
            items_settlement = json.loads(settlement_items_json)
            settled_batch = settle_consignment_batch(
                batch=batch,
                items_settlement=items_settlement,
                payout_method=payout_method,
                user=request.user,
                payout_reference=payout_reference,
                notes=notes,
                request=request,
            )
            messages.success(
                request,
                f"Pelunasan barang titipan batch {settled_batch.batch_number} berhasil! "
                f"Total bayar ke penitip: Rp {settled_batch.total_sold_cost:,.0f}"
            )
            return redirect('consignment_settle_receipt', uuid=settled_batch.uuid)
        except Exception as exc:
            messages.error(request, _exc_msg(exc))

    preview_data = get_consignment_settlement_preview(batch)

    return render(
        request,
        'inventory/consignment_settle_detail.html',
        {
            'batch': batch,
            'items': preview_data['items'],
            'total_received_val': preview_data['total_received_val'],
            'total_payable': preview_data['total_payable'],
            'total_retail': preview_data['total_retail'],
            'total_margin': preview_data['total_margin'],
            'is_settled': batch.status == ConsignmentBatch.STATUS_SETTLED,
        },
    )


@role_required(*STAFF_ROLES, perm='manage_consignments')
def consignment_settle_receipt(request, uuid):
    batch = get_object_or_404(
        ConsignmentBatch.objects.select_related('consignor', 'consignor__member', 'settled_by', 'created_by'),
        uuid=uuid,
    )
    items = batch.items.select_related('product', 'product__unit').all()

    return render(
        request,
        'inventory/consignment_settle_receipt.html',
        {
            'batch': batch,
            'items': items,
        },
    )


@role_required(*STAFF_ROLES, perm='manage_consignments')
@require_POST
def consignment_batch_cancel(request, uuid):
    batch = get_object_or_404(ConsignmentBatch, uuid=uuid)
    reason = request.POST.get('reason', '').strip()

    try:
        cancel_consignment_batch(batch, user=request.user, reason=reason)
        messages.success(request, f"Batch titipan {batch.batch_number} berhasil dibatalkan.")
    except Exception as exc:
        messages.error(request, _exc_msg(exc))

    return redirect('consignment_settlement_list')


@role_required(*STAFF_ROLES, perm='manage_consignments')
def consignment_report(request):
    today = timezone.localdate()
    date_from_raw = (request.GET.get('date_from') or '').strip()
    date_to_raw = (request.GET.get('date_to') or '').strip()
    consignor_id = (request.GET.get('consignor_id') or '').strip()

    date_from = timezone.datetime.fromisoformat(date_from_raw).date() if date_from_raw else today - timedelta(days=30)
    date_to = timezone.datetime.fromisoformat(date_to_raw).date() if date_to_raw else today

    qs = ConsignmentBatch.objects.select_related('consignor', 'settled_by').filter(
        batch_date__gte=date_from,
        batch_date__lte=date_to,
        status=ConsignmentBatch.STATUS_SETTLED,
    )

    if consignor_id:
        qs = qs.filter(consignor_id=consignor_id)

    qs = qs.order_by('-batch_date', '-created_at')

    aggregates = qs.aggregate(
        total_payout=Sum('total_sold_cost'),
        total_omzet=Sum('total_sold_retail'),
        total_margin=Sum('total_coop_margin'),
    )

    consignors = Consignor.objects.all().order_by('name')

    return render(
        request,
        'inventory/consignment_report.html',
        {
            'batches': qs,
            'consignors': consignors,
            'date_from': date_from,
            'date_to': date_to,
            'selected_consignor_id': consignor_id,
            'total_batches': qs.count(),
            'total_payout': aggregates['total_payout'] or Decimal('0.00'),
            'total_omzet': aggregates['total_omzet'] or Decimal('0.00'),
            'total_margin': aggregates['total_margin'] or Decimal('0.00'),
        },
    )
