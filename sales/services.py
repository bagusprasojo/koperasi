import json
from decimal import Decimal
from urllib import request as urllib_request
from urllib.error import URLError
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from inventory.models import Product
from inventory.services import post_pos_sale
from members.models import Member, MemberCard
from members.services import charge_member_by_card

from .models import ReceiptPrintJob, Sale, SaleItem, SalePayment


def _to_decimal(value, field_name='value'):
    try:
        return Decimal(str(value)).quantize(Decimal('0.01'))
    except Exception as exc:
        raise ValidationError(f'Format {field_name} tidak valid.') from exc


def get_default_member() -> Member:
    # Member default untuk transaksi walk-in non-member.
    member = Member.objects.filter(phone='0000000000').first()
    if member:
        return member
    return Member.objects.create(
        full_name='NON MEMBER',
        phone='0000000000',
        email='',
        address='Default walk-in customer',
        is_active=True,
    )


def search_members(keyword: str, limit: int = 10):
    q = (keyword or '').strip()
    qs = Member.objects.filter(is_active=True)
    if q:
        qs = qs.filter(
            Q(full_name__icontains=q) |
            Q(phone__icontains=q) |
            Q(card__card_number__icontains=q)
        )
    return qs.select_related('card').order_by('full_name')[:limit]


def build_price_preview(items: list):
    if not items:
        raise ValidationError('Item transaksi wajib diisi.')

    # Merge line jika produk sama (aturan scan berulang menambah qty).
    qty_by_product = {}
    for row in items:
        product_id = str(row.get('product_id', '')).strip()
        qty = int(row.get('qty', 0))
        if not product_id or qty <= 0:
            raise ValidationError('Item produk dan qty wajib valid.')
        qty_by_product[product_id] = qty_by_product.get(product_id, 0) + qty

    products = {
        str(p.id): p
        for p in Product.objects.prefetch_related('price_tiers').filter(id__in=list(qty_by_product.keys()))
    }

    lines = []
    subtotal = Decimal('0.00')
    for product_id, qty in qty_by_product.items():
        product = products.get(product_id)
        if not product:
            raise ValidationError('Produk tidak ditemukan.')
        selected_tier = product.price_tiers.filter(min_qty__lte=qty, max_qty__gte=qty).order_by('level').first()
        if not selected_tier:
            raise ValidationError(
                f'Qty {qty} untuk produk {product.name} tidak masuk range level harga manapun.'
            )
        unit_price = selected_tier.price
        line_total = (unit_price * Decimal(qty)).quantize(Decimal('0.01'))
        subtotal += line_total
        lines.append(
            {
                'product_id': product_id,
                'product_name': product.name,
                'qty': qty,
                'price_level': selected_tier.level,
                'unit_price': unit_price,
                'line_total': line_total,
            }
        )

    total = subtotal
    return {'lines': lines, 'subtotal': subtotal, 'total': total}


@transaction.atomic
def checkout_pos(*, member_id, items, payments, client_txn_id, user, card_number='', card_auth='', cash_received_raw='0'):
    if not client_txn_id:
        raise ValidationError('client_txn_id wajib diisi.')

    existing = Sale.objects.filter(client_txn_id=client_txn_id).first()
    if existing:
        return existing, False

    member = Member.objects.filter(id=member_id, is_active=True).first() if member_id else None
    if not member:
        member = get_default_member()

    preview = build_price_preview(items)
    total = preview['total']

    if not payments:
        raise ValidationError('Pembayaran wajib diisi.')

    parsed_payments = []
    paid_total = Decimal('0.00')
    cash_used_total = Decimal('0.00')
    for p in payments:
        method = (p.get('method') or '').strip()
        amount = _to_decimal(p.get('amount', '0'), 'amount')
        if method not in [SalePayment.METHOD_CASH, SalePayment.METHOD_MEMBER]:
            raise ValidationError('Metode pembayaran tidak valid.')
        if amount <= 0:
            raise ValidationError('Nominal pembayaran harus > 0.')
        parsed_payments.append({'method': method, 'amount': amount})
        paid_total += amount
        if method == SalePayment.METHOD_CASH:
            cash_used_total += amount

    if cash_used_total > 0:
        cash_received = _to_decimal(cash_received_raw, 'cash_received')
        if cash_received < cash_used_total:
            raise ValidationError('Uang tunai diterima tidak boleh kurang dari tunai yang dipakai.')
    else:
        cash_received = _to_decimal('0', 'cash_received')

    if paid_total != total:
        raise ValidationError('Total pembayaran harus sama dengan total belanja.')

    member_pay_total = sum((p['amount'] for p in parsed_payments if p['method'] == SalePayment.METHOD_MEMBER), Decimal('0.00'))
    if member_pay_total > 0:
        if not card_number:
            raise ValidationError('Nomor kartu wajib diisi untuk pembayaran deposit.')
        if not card_auth:
            raise ValidationError('Password user member wajib diisi untuk otorisasi deposit.')
        card = MemberCard.objects.select_related('member').filter(card_number=card_number).first()
        if not card:
            raise ValidationError('Kartu member tidak ditemukan.')
        if card.member_id != member.id:
            raise ValidationError('Kartu tidak sesuai dengan member transaksi.')
        if not card.member.user:
            raise ValidationError('Member belum memiliki akun user untuk otorisasi.')
        if not card.member.user.check_password(card_auth):
            raise ValidationError('Password user member tidak sesuai.')

    sale = Sale.objects.create(
        sale_number=f'SL-{uuid4().hex[:10].upper()}',
        client_txn_id=client_txn_id,
        member=member,
        subtotal=preview['subtotal'],
        total=preview['total'],
        created_by=user,
    )

    product_ids_for_stock_post = []
    for line in preview['lines']:
        product = Product.objects.get(id=line['product_id'])
        SaleItem.objects.create(
            sale=sale,
            product=product,
            qty=line['qty'],
            unit_price=line['unit_price'],
            line_total=line['line_total'],
        )
        product_ids_for_stock_post.append((product, line['qty']))

    cash_change = (cash_received - cash_used_total).quantize(Decimal('0.01')) if cash_used_total > 0 else Decimal('0.00')
    if cash_change < 0:
        raise ValidationError('Kembalian tidak valid.')

    for p in parsed_payments:
        reference = ''
        received_amount = p['amount']
        change_amount = Decimal('0.00')
        if p['method'] == SalePayment.METHOD_MEMBER:
            charge_member_by_card(
                card_number=card_number,
                amount=p['amount'],
                reference_code=sale.sale_number,
                description='Pembayaran POS',
            )
            reference = card_number
        elif p['method'] == SalePayment.METHOD_CASH:
            received_amount = cash_received
            change_amount = cash_change
        SalePayment.objects.create(
            sale=sale,
            method=p['method'],
            amount=p['amount'],
            received_amount=received_amount,
            change_amount=change_amount,
            reference=reference,
        )

    for product, qty in product_ids_for_stock_post:
        post_pos_sale(product=product, qty=qty, user=user, reference=sale.sale_number, note='Checkout POS')

    return sale, True


def get_receipt_detail(sale: Sale):
    items = [
        {
            'product_name': it.product.name,
            'qty': it.qty,
            'unit_price': str(it.unit_price),
            'line_total': str(it.line_total),
        }
        for it in sale.items.select_related('product').all()
    ]
    payments = [
        {
            'method': p.method,
            'amount': str(p.amount),
            'received_amount': str(p.received_amount),
            'change_amount': str(p.change_amount),
            'reference': p.reference,
        }
        for p in sale.payments.all()
    ]
    cash_payment = next((p for p in sale.payments.all() if p.method == SalePayment.METHOD_CASH), None)
    return {
        'sale_number': sale.sale_number,
        'sale_date': sale.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        'member_name': sale.member.full_name if sale.member else 'NON MEMBER',
        'subtotal': str(sale.subtotal),
        'total': str(sale.total),
        'cash_received': str(cash_payment.received_amount if cash_payment else Decimal('0.00')),
        'change_amount': str(cash_payment.change_amount if cash_payment else Decimal('0.00')),
        'items': items,
        'payments': payments,
    }


def _build_escpos_payload(sale: Sale, copies: int = 1):
    rc = get_receipt_detail(sale)
    lines = [
        'POS KOPERASI',
        f"No: {rc['sale_number']}",
        f"Tgl: {rc['sale_date']}",
        f"Member: {rc['member_name']}",
        '-------------------------------',
    ]
    for it in rc['items']:
        lines.append(f"{it['product_name']}")
        lines.append(f"{it['qty']} x {it['unit_price']} = {it['line_total']}")
    lines.append('-------------------------------')
    lines.append(f"TOTAL: {rc['total']}")
    lines.append(f"TUNAI DITERIMA: {rc['cash_received']}")
    lines.append(f"KEMBALIAN: {rc['change_amount']}")
    for p in rc['payments']:
        lines.append(f"BYR {p['method']}: {p['amount']}")
    lines.append('Terima kasih')
    return {
        'job_id': f'PRN-{uuid4().hex[:10].upper()}',
        'sale_number': sale.sale_number,
        'copies': copies,
        'printer_profile': 'escpos_generic_58mm',
        'lines': lines,
    }


def enqueue_receipt_print_job(sale: Sale, copies: int = 1, bridge_url: str = 'http://127.0.0.1:17971/print'):
    payload = _build_escpos_payload(sale=sale, copies=copies)
    return ReceiptPrintJob.objects.create(
        sale=sale,
        job_id=payload['job_id'],
        status=ReceiptPrintJob.STATUS_PENDING,
        attempts=0,
        copies=copies,
        bridge_url=bridge_url,
        payload_json=json.dumps(payload),
    )


def dispatch_receipt_print_job(job: ReceiptPrintJob):
    payload = json.loads(job.payload_json or '{}')
    req = urllib_request.Request(
        job.bridge_url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib_request.urlopen(req, timeout=3) as resp:
            raw = resp.read().decode('utf-8') if resp else '{}'
            job.attempts += 1
            job.status = ReceiptPrintJob.STATUS_SENT
            job.response_json = raw or '{}'
            job.last_error = ''
            job.save(update_fields=['attempts', 'status', 'response_json', 'last_error', 'updated_at'])
            return True, ''
    except URLError as exc:
        job.attempts += 1
        job.status = ReceiptPrintJob.STATUS_FAILED
        job.last_error = str(exc)
        job.save(update_fields=['attempts', 'status', 'last_error', 'updated_at'])
        return False, str(exc)
    except Exception as exc:
        job.attempts += 1
        job.status = ReceiptPrintJob.STATUS_FAILED
        job.last_error = str(exc)
        job.save(update_fields=['attempts', 'status', 'last_error', 'updated_at'])
        return False, str(exc)
