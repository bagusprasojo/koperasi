import json

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from inventory.models import Product
from core.decorators import role_required

from .services import build_price_preview, checkout_pos, get_default_member, search_members
from .services import (
    dispatch_receipt_print_job,
    enqueue_receipt_print_job,
    get_receipt_detail,
)
from .models import ReceiptPrintJob, Sale


@role_required('kasir', 'admin_toko')
def pos_page(request):
    products = Product.objects.select_related('unit').filter(price_tiers__level=1).distinct().order_by('name')
    default_member = get_default_member()
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
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)


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
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)


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
