import json
import csv
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.contrib import messages
from django.db.models import Count, Sum, Q
from django.db.models.functions import TruncDate
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST
from django.utils import timezone

from inventory.models import Product
from core.decorators import role_required

from .services import build_price_preview, checkout_pos, get_default_member, search_members
from .services import (
    dispatch_receipt_print_job,
    enqueue_receipt_print_job,
    get_receipt_detail,
)
from .models import ReceiptPrintJob, Sale, SalePayment, SaleItem


def _exc_message(exc):
    if isinstance(exc, ValidationError):
        return exc.messages[0] if exc.messages else str(exc)
    return str(exc)


def _parse_date_range(request):
    today = timezone.localdate()
    default_from = today - timedelta(days=6)
    date_from_raw = (request.GET.get('date_from') or '').strip()
    date_to_raw = (request.GET.get('date_to') or '').strip()
    date_from = default_from
    date_to = today
    if date_from_raw:
        date_from = timezone.datetime.fromisoformat(date_from_raw).date()
    if date_to_raw:
        date_to = timezone.datetime.fromisoformat(date_to_raw).date()
    if date_from > date_to:
        raise ValidationError('Periode tidak valid: tanggal awal lebih besar dari tanggal akhir.')
    return date_from, date_to


@role_required('kasir', 'admin_toko')
def pos_page(request):
    products = Product.objects.select_related('unit').filter(price_tiers__level=1).distinct().order_by('name')
    default_member = get_default_member()
    products_data = [
        {
            'id': str(p.id),
            'name': p.name,
            'sku': p.sku,
            'barcode': p.barcode or '',
            'unit': p.unit.name if p.unit else '-',
        }
        for p in products
    ]
    return render(
        request,
        'sales/pos_page.html',
        {
            'products_json': json.dumps(products_data),
            'default_member': default_member,
        },
    )


@role_required('kasir', 'admin_toko')
@require_GET
def pos_member_search_api(request):
    keyword = request.GET.get('q', '').strip()
    rows = search_members(keyword=keyword, limit=10)
    data = []
    for m in rows:
        card = getattr(m, 'card', None)
        data.append(
            {
                'id': m.id,
                'full_name': m.full_name,
                'phone': m.phone,
                'card_number': card.card_number if card else '',
            }
        )
    return JsonResponse({'success': True, 'data': data})


@role_required('kasir', 'admin_toko')
@require_POST
def pos_price_preview_api(request):
    try:
        payload = json.loads(request.body or '{}')
        items = payload.get('items', [])
        preview = build_price_preview(items)
        lines = [
            {
                'product_id': l['product_id'],
                'product_name': l['product_name'],
                'qty': l['qty'],
                'unit_price': str(l['unit_price']),
                'line_total': str(l['line_total']),
            }
            for l in preview['lines']
        ]
        return JsonResponse(
            {
                'success': True,
                'data': {
                    'lines': lines,
                    'subtotal': str(preview['subtotal']),
                    'total': str(preview['total']),
                },
            }
        )
    except Exception as exc:
        return JsonResponse({'success': False, 'message': _exc_message(exc)}, status=400)


@role_required('kasir', 'admin_toko')
@require_POST
def pos_checkout_api(request):
    try:
        payload = json.loads(request.body or '{}')
        sale, created = checkout_pos(
            member_id=payload.get('member_id'),
            items=payload.get('items', []),
            payments=payload.get('payments', []),
            client_txn_id=(payload.get('client_txn_id') or '').strip(),
            user=request.user,
            card_number=(payload.get('card_number') or '').strip(),
            card_auth=(payload.get('card_auth') or '').strip(),
            cash_received_raw=payload.get('cash_received', '0'),
        )
        print_status = {'queued': False, 'sent': False, 'message': ''}
        if created:
            job = enqueue_receipt_print_job(sale=sale, copies=1)
            sent, err = dispatch_receipt_print_job(job)
            print_status = {'queued': True, 'sent': sent, 'message': err}
        last_job = sale.print_jobs.order_by('-created_at').first()
        return JsonResponse(
            {
                'success': True,
                'data': {
                    'sale_number': sale.sale_number,
                    'sale_uuid': str(sale.uuid),
                    'idempotent_replay': not created,
                    'print_status': print_status,
                    'last_print_job': {
                        'job_id': last_job.job_id if last_job else '',
                        'status': last_job.status if last_job else '',
                        'attempts': last_job.attempts if last_job else 0,
                        'last_error': last_job.last_error if last_job else '',
                    },
                },
            }
        )
    except Exception as exc:
        return JsonResponse({'success': False, 'message': _exc_message(exc)}, status=400)


@role_required('kasir', 'admin_toko')
@require_GET
def pos_receipt_detail_api(request, sale_number):
    sale = Sale.objects.filter(sale_number=sale_number).first()
    if not sale:
        return JsonResponse({'success': False, 'message': 'Transaksi tidak ditemukan.'}, status=404)
    data = get_receipt_detail(sale)
    return JsonResponse({'success': True, 'data': data})


@role_required('kasir', 'admin_toko')
@require_POST
def pos_reprint_api(request, sale_number):
    sale = Sale.objects.filter(sale_number=sale_number).first()
    if not sale:
        return JsonResponse({'success': False, 'message': 'Transaksi tidak ditemukan.'}, status=404)
    job = enqueue_receipt_print_job(sale=sale, copies=1)
    sent, err = dispatch_receipt_print_job(job)
    return JsonResponse(
        {
            'success': sent,
            'message': '' if sent else err,
            'data': {
                'job_id': job.job_id,
                'status': job.status,
                'attempts': job.attempts,
                'last_error': job.last_error,
            },
        },
        status=200 if sent else 400,
    )


@role_required('kasir', 'admin_toko')
@require_POST
def pos_dispatch_pending_print_jobs_api(request):
    jobs = ReceiptPrintJob.objects.filter(status=ReceiptPrintJob.STATUS_FAILED).order_by('created_at')[:20]
    result = []
    for job in jobs:
        sent, err = dispatch_receipt_print_job(job)
        result.append({'job_id': job.job_id, 'sent': sent, 'error': err})
    return JsonResponse({'success': True, 'data': result})


@role_required('admin_toko', 'kasir')
def sales_daily_summary(request):
    try:
        date_from, date_to = _parse_date_range(request)
    except Exception as exc:
        return render(
            request,
            'sales/daily_summary.html',
            {
                'rows': [],
                'error_message': _exc_message(exc),
                'date_from': request.GET.get('date_from', ''),
                'date_to': request.GET.get('date_to', ''),
            },
        )

    sales_qs = Sale.objects.filter(created_at__date__gte=date_from, created_at__date__lte=date_to)
    payment_qs = SalePayment.objects.filter(sale__created_at__date__gte=date_from, sale__created_at__date__lte=date_to)

    base_rows = list(
        sales_qs.annotate(tx_date=TruncDate('created_at'))
        .values('tx_date')
        .annotate(
            total_transactions=Count('id'),
            omzet=Sum('total'),
            member_transactions=Count('id', filter=Q(member__isnull=False)),
            non_member_transactions=Count('id', filter=Q(member__isnull=True)),
        )
        .order_by('-tx_date')
    )
    item_rows = {
        row['tx_date']: row['item_qty']
        for row in sales_qs.annotate(tx_date=TruncDate('created_at'))
        .values('tx_date')
        .annotate(item_qty=Sum('items__qty'))
    }
    cash_rows = {
        row['tx_date']: row['amount']
        for row in payment_qs.filter(method=SalePayment.METHOD_CASH)
        .annotate(tx_date=TruncDate('sale__created_at'))
        .values('tx_date')
        .annotate(amount=Sum('amount'))
    }
    deposit_rows = {
        row['tx_date']: row['amount']
        for row in payment_qs.filter(method=SalePayment.METHOD_MEMBER)
        .annotate(tx_date=TruncDate('sale__created_at'))
        .values('tx_date')
        .annotate(amount=Sum('amount'))
    }
    split_rows = {
        row['tx_date']: row['split_count']
        for row in payment_qs.annotate(tx_date=TruncDate('sale__created_at'))
        .values('tx_date', 'sale_id')
        .annotate(method_count=Count('method', distinct=True))
        .filter(method_count__gte=2)
        .values('tx_date')
        .annotate(split_count=Count('sale_id'))
    }

    rows = []
    for r in base_rows:
        tx_date = r['tx_date']
        total_tx = r['total_transactions'] or 0
        omzet = r['omzet'] or Decimal('0.00')
        avg_tx = (omzet / total_tx).quantize(Decimal('0.01')) if total_tx > 0 else Decimal('0.00')
        rows.append(
            {
                'tx_date': tx_date,
                'total_transactions': total_tx,
                'item_qty': item_rows.get(tx_date) or 0,
                'omzet': omzet,
                'cash_amount': cash_rows.get(tx_date) or Decimal('0.00'),
                'deposit_amount': deposit_rows.get(tx_date) or Decimal('0.00'),
                'split_count': split_rows.get(tx_date) or 0,
                'avg_transaction': avg_tx,
                'member_transactions': r['member_transactions'] or 0,
                'non_member_transactions': r['non_member_transactions'] or 0,
            }
        )

    total_days = len(rows)
    total_transactions = sum(r['total_transactions'] for r in rows)
    total_omzet = sum((r['omzet'] for r in rows), Decimal('0.00'))
    total_cash = sum((r['cash_amount'] for r in rows), Decimal('0.00'))
    total_deposit = sum((r['deposit_amount'] for r in rows), Decimal('0.00'))
    avg_omzet_per_day = (total_omzet / total_days).quantize(Decimal('0.01')) if total_days > 0 else Decimal('0.00')
    avg_transaction_value = (total_omzet / total_transactions).quantize(Decimal('0.01')) if total_transactions > 0 else Decimal('0.00')

    return render(
        request,
        'sales/daily_summary.html',
        {
            'rows': rows,
            'date_from': date_from.isoformat(),
            'date_to': date_to.isoformat(),
            'kpi': {
                'total_days': total_days,
                'total_transactions': total_transactions,
                'total_omzet': total_omzet,
                'total_cash': total_cash,
                'total_deposit': total_deposit,
                'avg_omzet_per_day': avg_omzet_per_day,
                'avg_transaction_value': avg_transaction_value,
            },
        },
    )


@role_required('admin_toko', 'kasir')
def sales_daily_summary_detail(request, tx_date):
    try:
        target_date = timezone.datetime.fromisoformat(tx_date).date()
    except Exception:
        messages.error(request, 'Format tanggal detail laporan tidak valid.')
        return render(request, 'sales/daily_summary_detail.html', {'rows': [], 'tx_date': tx_date, 'query': ''})
    query = (request.GET.get('q') or '').strip()
    sales = Sale.objects.select_related('member', 'created_by').prefetch_related('payments').filter(created_at__date=target_date).order_by('-created_at')
    if query:
        sales = sales.filter(
            Q(sale_number__icontains=query) |
            Q(member__full_name__icontains=query) |
            Q(created_by__username__icontains=query)
        )
    rows = []
    for s in sales:
        methods = sorted(set(p.get_method_display() for p in s.payments.all()))
        rows.append({'sale': s, 'methods': ', '.join(methods) if methods else '-'})
    return render(
        request,
        'sales/daily_summary_detail.html',
        {'rows': rows, 'tx_date': target_date, 'query': query},
    )


@role_required('admin_toko', 'kasir')
def sales_daily_summary_export_csv(request):
    try:
        date_from, date_to = _parse_date_range(request)
    except Exception as exc:
        return HttpResponse(f'Parameter periode tidak valid: {_exc_message(exc)}', status=400)
    sales_qs = Sale.objects.filter(created_at__date__gte=date_from, created_at__date__lte=date_to)
    payment_qs = SalePayment.objects.filter(sale__created_at__date__gte=date_from, sale__created_at__date__lte=date_to)

    base_rows = list(
        sales_qs.annotate(tx_date=TruncDate('created_at'))
        .values('tx_date')
        .annotate(
            total_transactions=Count('id'),
            omzet=Sum('total'),
            member_transactions=Count('id', filter=Q(member__isnull=False)),
            non_member_transactions=Count('id', filter=Q(member__isnull=True)),
            item_qty=Sum('items__qty'),
        )
        .order_by('-tx_date')
    )
    cash_rows = {
        row['tx_date']: row['amount']
        for row in payment_qs.filter(method=SalePayment.METHOD_CASH)
        .annotate(tx_date=TruncDate('sale__created_at'))
        .values('tx_date')
        .annotate(amount=Sum('amount'))
    }
    deposit_rows = {
        row['tx_date']: row['amount']
        for row in payment_qs.filter(method=SalePayment.METHOD_MEMBER)
        .annotate(tx_date=TruncDate('sale__created_at'))
        .values('tx_date')
        .annotate(amount=Sum('amount'))
    }

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="rekap_penjualan_harian_{date_from}_{date_to}.csv"'
    writer = csv.writer(response)
    writer.writerow(['tanggal', 'jumlah_transaksi', 'qty_item', 'omzet', 'tunai', 'deposit', 'member_trx', 'non_member_trx'])
    for row in base_rows:
        tx_date = row['tx_date']
        writer.writerow([
            tx_date,
            row['total_transactions'] or 0,
            row['item_qty'] or 0,
            row['omzet'] or Decimal('0.00'),
            cash_rows.get(tx_date) or Decimal('0.00'),
            deposit_rows.get(tx_date) or Decimal('0.00'),
            row['member_transactions'] or 0,
            row['non_member_transactions'] or 0,
        ])
    return response


@role_required('admin_toko', 'kasir', 'member')
def sales_transaction_detail(request, sale_number):
    sale = Sale.objects.select_related('member', 'created_by').prefetch_related('items__product', 'payments').filter(sale_number=sale_number).first()
    if not sale:
        messages.error(request, 'Transaksi tidak ditemukan.')
        return render(request, 'sales/transaction_detail.html', {'sale': None})
    # Member hanya boleh membuka transaksi miliknya sendiri.
    user_roles = set(request.user.groups.values_list('name', flat=True))
    if 'member' in user_roles and 'admin_toko' not in user_roles and 'kasir' not in user_roles:
        member_profile = getattr(request.user, 'member_profile', None)
        if not member_profile or sale.member_id != member_profile.id:
            messages.error(request, 'Anda tidak memiliki akses ke detail transaksi ini.')
            return render(request, 'sales/transaction_detail.html', {'sale': None})
    return render(request, 'sales/transaction_detail.html', {'sale': sale})


def _parse_int(raw, default_value):
    try:
        val = int(str(raw).strip())
        return val if val > 0 else default_value
    except Exception:
        return default_value


def _build_product_report_rows(*, date_from, date_to, category_id='', sort_mode='top', sort_by='qty', limit=25):
    qs = SaleItem.objects.select_related('sale', 'product', 'product__category').filter(
        sale__created_at__date__gte=date_from,
        sale__created_at__date__lte=date_to,
    )
    if category_id:
        qs = qs.filter(product__category_id=category_id)

    rows = list(
        qs.values(
            'product_id',
            'product__name',
            'product__sku',
            'product__category__name',
        )
        .annotate(
            qty=Sum('qty'),
            omzet=Sum('line_total'),
        )
    )
    total_omzet = sum((r['omzet'] or Decimal('0.00') for r in rows), Decimal('0.00'))
    for r in rows:
        omzet = r['omzet'] or Decimal('0.00')
        qty = r['qty'] or 0
        if total_omzet > 0:
            kontribusi = (omzet / total_omzet) * Decimal('100')
            r['kontribusi_pct'] = kontribusi.quantize(Decimal('0.01'))
        else:
            r['kontribusi_pct'] = Decimal('0.00')
        r['avg_price'] = (omzet / qty).quantize(Decimal('0.01')) if qty else Decimal('0.00')

    sort_key = {
        'qty': lambda x: x['qty'] or 0,
        'omzet': lambda x: x['omzet'] or Decimal('0.00'),
        'kontribusi': lambda x: x['kontribusi_pct'] or Decimal('0.00'),
    }.get(sort_by, lambda x: x['qty'] or 0)
    reverse = sort_mode != 'bottom'
    rows = sorted(rows, key=lambda x: (sort_key(x), x['product__name'] or ''), reverse=reverse)
    rows = rows[:limit]
    total_qty = sum((r['qty'] or 0 for r in rows))
    return rows, total_omzet, total_qty


@role_required('admin_toko', 'kasir', 'pembelian')
def sales_product_report(request):
    try:
        date_from, date_to = _parse_date_range(request)
    except Exception as exc:
        return render(
            request,
            'sales/product_report.html',
            {
                'rows': [],
                'error_message': _exc_message(exc),
                'date_from': request.GET.get('date_from', ''),
                'date_to': request.GET.get('date_to', ''),
                'categories': Product.objects.values('category_id', 'category__name').distinct().order_by('category__name'),
            },
        )

    category_id = (request.GET.get('category_id') or '').strip()
    sort_mode = (request.GET.get('sort_mode') or 'top').strip()
    sort_by = (request.GET.get('sort_by') or 'qty').strip()
    limit = _parse_int(request.GET.get('limit', 25), 25)
    if sort_mode not in ['top', 'bottom']:
        sort_mode = 'top'
    if sort_by not in ['qty', 'omzet', 'kontribusi']:
        sort_by = 'qty'

    rows, total_omzet, total_qty = _build_product_report_rows(
        date_from=date_from,
        date_to=date_to,
        category_id=category_id,
        sort_mode=sort_mode,
        sort_by=sort_by,
        limit=limit,
    )
    distinct_products = len(rows)
    avg_omzet_per_product = (total_omzet / distinct_products).quantize(Decimal('0.01')) if distinct_products else Decimal('0.00')
    categories = Product.objects.select_related('category').values('category_id', 'category__name').exclude(category_id__isnull=True).distinct().order_by('category__name')
    return render(
        request,
        'sales/product_report.html',
        {
            'rows': rows,
            'date_from': date_from.isoformat(),
            'date_to': date_to.isoformat(),
            'category_id': category_id,
            'sort_mode': sort_mode,
            'sort_by': sort_by,
            'limit': limit,
            'categories': categories,
            'kpi': {
                'distinct_products': distinct_products,
                'total_qty': total_qty,
                'total_omzet': total_omzet,
                'avg_omzet_per_product': avg_omzet_per_product,
            },
        },
    )


@role_required('admin_toko', 'kasir', 'pembelian')
def sales_product_report_export_csv(request):
    date_from, date_to = _parse_date_range(request)
    category_id = (request.GET.get('category_id') or '').strip()
    sort_mode = (request.GET.get('sort_mode') or 'top').strip()
    sort_by = (request.GET.get('sort_by') or 'qty').strip()
    limit = _parse_int(request.GET.get('limit', 25), 25)
    rows, _, _ = _build_product_report_rows(
        date_from=date_from,
        date_to=date_to,
        category_id=category_id,
        sort_mode=sort_mode,
        sort_by=sort_by,
        limit=limit,
    )
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="laporan_produk_terjual_{date_from}_{date_to}.csv"'
    writer = csv.writer(response)
    writer.writerow(['produk', 'sku', 'kategori', 'qty', 'omzet', 'kontribusi_pct'])
    for r in rows:
        writer.writerow([
            r.get('product__name') or '',
            r.get('product__sku') or '',
            r.get('product__category__name') or '-',
            r.get('qty') or 0,
            r.get('omzet') or Decimal('0.00'),
            r.get('kontribusi_pct') or Decimal('0.00'),
        ])
    return response
