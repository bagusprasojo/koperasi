import json
import csv
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.contrib import messages
from django.db.models import Count, Sum, Q
from django.db.models.functions import TruncDate
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST
from django.utils import timezone

from inventory.models import InventoryTransaction, Product, StockLedger
from core.decorators import role_required

from .services import build_price_preview, checkout_pos, get_default_member, search_members
from .services import (
    build_escpos_payload,
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


def _parse_month_period(request):
    today = timezone.localdate()
    try:
        selected_month = int(request.GET.get('month') or today.month)
        selected_year = int(request.GET.get('year') or today.year)
        period_start = date(selected_year, selected_month, 1)
    except (TypeError, ValueError):
        selected_month = today.month
        selected_year = today.year
        period_start = date(selected_year, selected_month, 1)
    period_end = date(selected_year, selected_month, monthrange(selected_year, selected_month)[1])
    return selected_month, selected_year, period_start, period_end


def _month_options():
    labels = [
        'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
        'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember',
    ]
    return [{'value': i, 'label': labels[i - 1]} for i in range(1, 13)]


def _money(value):
    return value or Decimal('0.00')


def _profit_loss_data(request):
    selected_month, selected_year, period_start, period_end = _parse_month_period(request)
    sales_total = _money(
        Sale.objects
        .filter(created_at__date__gte=period_start, created_at__date__lte=period_end)
        .aggregate(v=Sum('total'))['v']
    )
    cogs_sold = _money(
        StockLedger.objects
        .filter(
            tx_date__gte=period_start,
            tx_date__lte=period_end,
            tx__tx_type=InventoryTransaction.TYPE_POS_SALE,
        )
        .aggregate(v=Sum('value_out'))['v']
    )
    internal_used = _money(
        StockLedger.objects
        .filter(
            tx_date__gte=period_start,
            tx_date__lte=period_end,
            tx__tx_type=InventoryTransaction.TYPE_INTERNAL_USED,
        )
        .aggregate(v=Sum('value_out'))['v']
    )
    stock_opname_minus = _money(
        StockLedger.objects
        .filter(
            tx_date__gte=period_start,
            tx_date__lte=period_end,
            tx__tx_type=InventoryTransaction.TYPE_STOCK_OPNAME,
            qty_out__gt=0,
        )
        .aggregate(v=Sum('value_out'))['v']
    )
    gross_profit = sales_total - cogs_sold
    temporary_operating_profit = gross_profit - internal_used - stock_opname_minus
    rows = [
        {'section': 'Pendapatan Penjualan', 'label': 'Total Omzet POS', 'amount': sales_total, 'level': 1, 'sign': 'positive'},
        {'section': 'HPP', 'label': 'HPP Barang Terjual', 'amount': cogs_sold, 'level': 1, 'sign': 'negative'},
        {'section': '', 'label': 'Laba Kotor', 'amount': gross_profit, 'level': 0, 'sign': 'total'},
        {'section': 'Penyesuaian Operasional', 'label': 'Internal Used', 'amount': internal_used, 'level': 1, 'sign': 'negative'},
        {'section': '', 'label': 'Selisih Stock Opname Minus', 'amount': stock_opname_minus, 'level': 1, 'sign': 'negative'},
        {'section': '', 'label': 'Laba Operasional Sementara', 'amount': temporary_operating_profit, 'level': 0, 'sign': 'grand_total'},
    ]
    return {
        'selected_month': selected_month,
        'selected_year': selected_year,
        'period_start': period_start,
        'period_end': period_end,
        'rows': rows,
        'summary': {
            'sales_total': sales_total,
            'cogs_sold': cogs_sold,
            'gross_profit': gross_profit,
            'internal_used': internal_used,
            'stock_opname_minus': stock_opname_minus,
            'temporary_operating_profit': temporary_operating_profit,
        },
    }


def _format_money(value):
    return f'{Decimal(value or 0):,.2f}'


def _pdf_escape(text):
    return str(text).replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def _simple_text_pdf(lines):
    page_width = 595
    page_height = 842
    left = 48
    top = 790
    line_height = 15
    commands = ['BT', '/F1 10 Tf', f'{left} {top} Td']
    for idx, line in enumerate(lines):
        if idx:
            commands.append(f'0 -{line_height} Td')
        commands.append(f'({_pdf_escape(line)}) Tj')
    commands.append('ET')
    stream = '\n'.join(commands)
    objects = [
        '<< /Type /Catalog /Pages 2 0 R >>',
        '<< /Type /Pages /Kids [4 0 R] /Count 1 >>',
        '<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>',
        (
            f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] '
            f'/Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>'
        ),
        f'<< /Length {len(stream.encode("latin-1", "replace"))} >>\nstream\n{stream}\nendstream',
    ]
    content = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for idx, body in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f'{idx} 0 obj\n'.encode('latin-1'))
        content.extend(body.encode('latin-1', 'replace'))
        content.extend(b'\nendobj\n')
    xref_offset = len(content)
    content.extend(f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode('latin-1'))
    for offset in offsets[1:]:
        content.extend(f'{offset:010d} 00000 n \n'.encode('latin-1'))
    content.extend(f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n'.encode('latin-1'))
    return bytes(content)


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
        receipt_payload = build_escpos_payload(sale=sale, copies=1)
        return JsonResponse(
            {
                'success': True,
                'data': {
                    'sale_number': sale.sale_number,
                    'sale_uuid': str(sale.uuid),
                    'idempotent_replay': not created,
                    'receipt_payload': receipt_payload,
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
    return JsonResponse(
        {
            'success': True,
            'data': {
                'receipt_payload': build_escpos_payload(sale=sale, copies=1),
            },
        },
        status=200,
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


@role_required('admin_toko')
def profit_loss_report(request):
    data = _profit_loss_data(request)
    today = timezone.localdate()
    return render(
        request,
        'sales/profit_loss_report.html',
        {
            **data,
            'month_options': _month_options(),
            'year_options': range(today.year - 5, today.year + 2),
        },
    )


@role_required('admin_toko')
def profit_loss_report_export_pdf(request):
    data = _profit_loss_data(request)
    lines = [
        'LAPORAN LABA RUGI OPERASIONAL',
        f'Periode: {data["period_start"].strftime("%d-%m-%Y")} s/d {data["period_end"].strftime("%d-%m-%Y")}',
        '',
    ]
    current_section = None
    for row in data['rows']:
        if row['section'] and row['section'] != current_section:
            current_section = row['section']
            lines.append(row['section'])
        amount = _format_money(row['amount'])
        if row['sign'] == 'negative':
            amount = f'({_format_money(row["amount"])})'
        label = f'    {row["label"]}' if row['level'] else row['label']
        lines.append(f'{label:<42} {amount:>18}')
        if row['sign'] in ['total', 'grand_total']:
            lines.append('')
    lines.extend(
        [
            '',
            'Catatan:',
            '- Laporan ini belum memasukkan biaya operasional umum, pajak, retur, dan pendapatan lain-lain.',
            '- Stock opname plus tidak dimasukkan sebagai pendapatan pada versi konservatif ini.',
        ]
    )
    pdf_bytes = _simple_text_pdf(lines)
    filename = f'laba_rugi_operasional_{data["selected_year"]}_{data["selected_month"]:02d}.pdf'
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


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
