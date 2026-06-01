from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum, F

from members.models import Member, MemberLedger, MemberWallet

from .models import (
    DailyClosing,
    InventoryTransaction,
    InventoryTransactionItem,
    Supplier,
    MemberDailySnapshot,
    Product,
    ProductDailySnapshot,
    StockLedger,
)


def _tx_number(prefix: str) -> str:
    return f'{prefix}-{date.today().strftime("%Y%m%d")}-{uuid4().hex[:8].upper()}'


def _ensure_not_closed(tx_date: date):
    if DailyClosing.objects.filter(close_date=tx_date, is_locked=True).exists():
        raise ValidationError('Transaksi tidak bisa diproses karena tanggal tersebut sudah tutup harian.')


def _rebuild_product_stock_from_ledgers(product: Product):
    """
    Rebuild saldo stok produk dari seluruh StockLedger berdasarkan urutan kronologis
    (tx_date lalu waktu transaksi) untuk mencegah drift saat ada edit/hapus/backdate.
    """
    balance = 0
    ledgers = (
        StockLedger.objects
        .select_related('tx')
        .filter(product=product)
        .order_by('tx_date', 'tx__created_at', 'created_at', 'id')
    )
    for ledger in ledgers:
        qty_in = int(ledger.qty_in or 0)
        qty_out = int(ledger.qty_out or 0)
        before = balance
        after = before + qty_in - qty_out
        ledger.balance_before = before
        ledger.balance_after = after
        ledger.save(update_fields=['balance_before', 'balance_after', 'updated_at'])
        balance = after

    latest_purchase_item = (
        InventoryTransactionItem.objects
        .select_related('transaction')
        .filter(
            product=product,
            transaction__tx_type=InventoryTransaction.TYPE_PURCHASE,
        )
        .order_by('-transaction__tx_date', '-transaction__created_at', '-created_at')
        .first()
    )
    if latest_purchase_item:
        product.last_purchase_price = latest_purchase_item.unit_cost
    product.stock = balance
    product.save(update_fields=['stock', 'last_purchase_price', 'updated_at'])


@transaction.atomic
def post_purchase(product: Product, qty: int, unit_cost: Decimal, user, note=''):
    _ensure_not_closed(date.today())
    if qty <= 0:
        raise ValidationError('Qty pembelian harus > 0.')
    if unit_cost <= 0:
        raise ValidationError('Harga beli harus > 0.')
    tx = InventoryTransaction.objects.create(
        tx_number=_tx_number('PUR'),
        tx_type=InventoryTransaction.TYPE_PURCHASE,
        tx_date=date.today(),
        note=note,
        created_by=user,
    )
    total = (unit_cost * Decimal(qty)).quantize(Decimal('0.01'))
    InventoryTransactionItem.objects.create(
        transaction=tx,
        product=product,
        qty=qty,
        unit_cost=unit_cost,
        total_cost=total,
    )
    before = product.stock
    after = before + qty
    StockLedger.objects.create(
        product=product,
        tx=tx,
        tx_date=tx.tx_date,
        qty_in=qty,
        qty_out=0,
        balance_before=before,
        balance_after=after,
        unit_cost_at_txn=unit_cost,
        value_in=total,
        value_out=0,
        note=note or 'Pembelian',
    )
    _rebuild_product_stock_from_ledgers(product)
    return tx


@transaction.atomic
def create_purchase_transaction(supplier: Supplier, tx_date: date, items: list, user, note=''):
    if not items:
        raise ValidationError('Item pembelian wajib diisi.')
    _ensure_not_closed(tx_date)
    tx = InventoryTransaction.objects.create(
        tx_number=_tx_number('PUR'),
        tx_type=InventoryTransaction.TYPE_PURCHASE,
        tx_date=tx_date,
        supplier=supplier,
        note=note,
        created_by=user,
    )
    grand_total = Decimal('0.00')
    affected_products = set()
    for item in items:
        product = item['product']
        affected_products.add(product.id)
        qty = int(item['qty'])
        unit_cost = Decimal(item['unit_cost'])
        if qty <= 0 or unit_cost <= 0:
            raise ValidationError('Qty dan harga beli harus lebih besar dari 0.')
        total = (unit_cost * Decimal(qty)).quantize(Decimal('0.01'))
        InventoryTransactionItem.objects.create(
            transaction=tx,
            product=product,
            qty=qty,
            unit_cost=unit_cost,
            total_cost=total,
        )
        before = product.stock
        after = before + qty
        StockLedger.objects.create(
            product=product,
            tx=tx,
            tx_date=tx_date,
            qty_in=qty,
            qty_out=0,
            balance_before=before,
            balance_after=after,
            unit_cost_at_txn=unit_cost,
            value_in=total,
            value_out=0,
            note=note or 'Pembelian',
        )
        grand_total += total
    for product_id in affected_products:
        p = Product.objects.get(id=product_id)
        _rebuild_product_stock_from_ledgers(p)
    tx.total_amount = grand_total
    tx.save(update_fields=['total_amount', 'updated_at'])
    return tx


@transaction.atomic
def edit_purchase_transaction(tx: InventoryTransaction, supplier: Supplier, tx_date: date, items: list, user, note=''):
    if tx.tx_type != InventoryTransaction.TYPE_PURCHASE:
        raise ValidationError('Hanya transaksi pembelian yang bisa diedit.')
    _ensure_not_closed(tx.tx_date)
    _ensure_not_closed(tx_date)
    old_product_ids = set(tx.items.values_list('product_id', flat=True))
    new_product_ids = {item['product'].id for item in items}
    affected_product_ids = old_product_ids.union(new_product_ids)
    tx.stock_ledgers.all().delete()
    tx.items.all().delete()

    tx.supplier = supplier
    tx.tx_date = tx_date
    tx.note = note
    tx.created_by = user
    tx.save(update_fields=['supplier', 'tx_date', 'note', 'created_by', 'updated_at'])

    grand_total = Decimal('0.00')
    for item in items:
        product = item['product']
        qty = int(item['qty'])
        unit_cost = Decimal(item['unit_cost'])
        if qty <= 0 or unit_cost <= 0:
            raise ValidationError('Qty dan harga beli harus lebih besar dari 0.')
        total = (unit_cost * Decimal(qty)).quantize(Decimal('0.01'))
        InventoryTransactionItem.objects.create(
            transaction=tx,
            product=product,
            qty=qty,
            unit_cost=unit_cost,
            total_cost=total,
        )
        before = product.stock
        after = before + qty
        StockLedger.objects.create(
            product=product,
            tx=tx,
            tx_date=tx_date,
            qty_in=qty,
            qty_out=0,
            balance_before=before,
            balance_after=after,
            unit_cost_at_txn=unit_cost,
            value_in=total,
            value_out=0,
            note=note or 'Pembelian',
        )
        grand_total += total
    for product_id in affected_product_ids:
        p = Product.objects.get(id=product_id)
        _rebuild_product_stock_from_ledgers(p)
    tx.total_amount = grand_total
    tx.save(update_fields=['total_amount', 'updated_at'])
    return tx


@transaction.atomic
def delete_purchase_transaction(tx: InventoryTransaction):
    if tx.tx_type != InventoryTransaction.TYPE_PURCHASE:
        raise ValidationError('Hanya transaksi pembelian yang bisa dihapus.')
    _ensure_not_closed(tx.tx_date)
    affected_product_ids = set(tx.items.values_list('product_id', flat=True))
    tx.delete()
    for product_id in affected_product_ids:
        p = Product.objects.get(id=product_id)
        _rebuild_product_stock_from_ledgers(p)


@transaction.atomic
def post_internal_used(product: Product, qty: int, user, note=''):
    _ensure_not_closed(date.today())
    if qty <= 0:
        raise ValidationError('Qty internal used harus > 0.')
    if product.stock < qty:
        raise ValidationError('Stok tidak mencukupi.')
    unit_cost = product.cost_of_goods_sold
    if unit_cost <= 0:
        raise ValidationError('HPP produk harus lebih besar dari 0 untuk transaksi internal used.')
    tx = InventoryTransaction.objects.create(
        tx_number=_tx_number('IUS'),
        tx_type=InventoryTransaction.TYPE_INTERNAL_USED,
        tx_date=date.today(),
        note=note,
        created_by=user,
    )
    total = (unit_cost * Decimal(qty)).quantize(Decimal('0.01'))
    InventoryTransactionItem.objects.create(
        transaction=tx,
        product=product,
        qty=-qty,
        unit_cost=unit_cost,
        total_cost=total,
    )
    before = product.stock
    after = before - qty
    StockLedger.objects.create(
        product=product,
        tx=tx,
        tx_date=tx.tx_date,
        qty_in=0,
        qty_out=qty,
        balance_before=before,
        balance_after=after,
        unit_cost_at_txn=unit_cost,
        value_in=0,
        value_out=total,
        note=note or 'Internal used',
    )
    _rebuild_product_stock_from_ledgers(product)
    return tx


@transaction.atomic
def post_pos_sale(product: Product, qty: int, user, reference='', note=''):
    _ensure_not_closed(date.today())
    if qty <= 0:
        raise ValidationError('Qty penjualan harus > 0.')
    if product.stock < qty:
        raise ValidationError('Stok tidak mencukupi.')
    unit_cost = product.cost_of_goods_sold
    if unit_cost <= 0:
        raise ValidationError('HPP produk harus lebih besar dari 0 untuk transaksi penjualan POS.')
    tx = InventoryTransaction.objects.create(
        tx_number=_tx_number('SAL'),
        tx_type=InventoryTransaction.TYPE_POS_SALE,
        tx_date=date.today(),
        reference=reference,
        note=note,
        created_by=user,
    )
    total = (unit_cost * Decimal(qty)).quantize(Decimal('0.01'))
    InventoryTransactionItem.objects.create(
        transaction=tx,
        product=product,
        qty=-qty,
        unit_cost=unit_cost,
        total_cost=total,
    )
    before = product.stock
    after = before - qty
    StockLedger.objects.create(
        product=product,
        tx=tx,
        tx_date=tx.tx_date,
        qty_in=0,
        qty_out=qty,
        balance_before=before,
        balance_after=after,
        unit_cost_at_txn=unit_cost,
        value_in=0,
        value_out=total,
        note=note or 'Penjualan POS',
    )
    _rebuild_product_stock_from_ledgers(product)
    return tx


@transaction.atomic
def post_stock_opname(product: Product, actual_stock: int, user, note=''):
    _ensure_not_closed(date.today())
    if actual_stock < 0:
        raise ValidationError('Stok aktual tidak boleh negatif.')
    diff = actual_stock - product.stock
    tx = InventoryTransaction.objects.create(
        tx_number=_tx_number('SOP'),
        tx_type=InventoryTransaction.TYPE_STOCK_OPNAME,
        tx_date=date.today(),
        note=note,
        created_by=user,
    )
    unit_cost = product.cost_of_goods_sold
    if unit_cost <= 0 and diff != 0:
        raise ValidationError('HPP produk harus lebih besar dari 0 untuk transaksi stock opname.')
    total = (unit_cost * Decimal(abs(diff))).quantize(Decimal('0.01'))
    InventoryTransactionItem.objects.create(
        transaction=tx,
        product=product,
        qty=diff,
        unit_cost=unit_cost,
        total_cost=total,
    )
    before = product.stock
    after = actual_stock
    StockLedger.objects.create(
        product=product,
        tx=tx,
        tx_date=tx.tx_date,
        qty_in=max(diff, 0),
        qty_out=max(-diff, 0),
        balance_before=before,
        balance_after=after,
        unit_cost_at_txn=unit_cost,
        value_in=total if diff > 0 else 0,
        value_out=total if diff < 0 else 0,
        note=note or 'Stock opname',
    )
    _rebuild_product_stock_from_ledgers(product)
    return tx


@transaction.atomic
def close_daily(closing_date: date, user, note=''):
    today = date.today()
    if closing_date > today:
        raise ValidationError('Tanggal tutup harian tidak boleh melewati hari ini.')
    if DailyClosing.objects.filter(close_date=closing_date).exists():
        raise ValidationError('Tanggal ini sudah ditutup.')
    latest = DailyClosing.objects.order_by('-close_date').first()
    if latest:
        expected_next_date = latest.close_date + timedelta(days=1)
        if closing_date != expected_next_date:
            raise ValidationError(
                f'Closing harus berurutan. Tanggal berikutnya yang valid adalah {expected_next_date}.'
            )
    prev = latest
    closing = DailyClosing.objects.create(
        close_date=closing_date,
        prev_close_date=prev.close_date if prev else None,
        note=note,
        closed_by=user,
        is_locked=True,
    )
    products = Product.objects.all()
    for p in products:
        prev_snap = ProductDailySnapshot.objects.filter(closing=prev, product=p).first() if prev else None
        opening = prev_snap.closing_stock if prev_snap else 0
        mut_in = StockLedger.objects.filter(product=p, tx_date=closing_date).aggregate(v=Sum('qty_in'))['v'] or 0
        mut_out = StockLedger.objects.filter(product=p, tx_date=closing_date).aggregate(v=Sum('qty_out'))['v'] or 0
        ProductDailySnapshot.objects.create(
            closing=closing,
            product=p,
            opening_stock=opening,
            mutation_in=mut_in,
            mutation_out=mut_out,
            closing_stock=opening + mut_in - mut_out,
        )
    members = Member.objects.all()
    in_types = [MemberLedger.TYPE_TOPUP, MemberLedger.TYPE_REFUND, MemberLedger.TYPE_REVERSAL_WITHDRAWAL]
    out_types = [MemberLedger.TYPE_PURCHASE, MemberLedger.TYPE_REVERSAL_TOPUP, MemberLedger.TYPE_WITHDRAWAL]
    for m in members:
        prev_snap = MemberDailySnapshot.objects.filter(closing=prev, member=m).first() if prev else None
        opening = prev_snap.closing_balance if prev_snap else Decimal('0.00')
        day_ledgers = MemberLedger.objects.filter(member=m, created_at__date=closing_date)
        mut_in = day_ledgers.filter(txn_type__in=in_types).aggregate(v=Sum('amount'))['v'] or Decimal('0.00')
        mut_out = day_ledgers.filter(txn_type__in=out_types).aggregate(v=Sum('amount'))['v'] or Decimal('0.00')
        MemberDailySnapshot.objects.create(
            closing=closing,
            member=m,
            opening_balance=opening,
            mutation_in=mut_in,
            mutation_out=mut_out,
            closing_balance=opening + mut_in - mut_out,
        )
        wallet = MemberWallet.objects.filter(member=m).first()
        if wallet:
            wallet.balance = opening + mut_in - mut_out
            wallet.save(update_fields=['balance', 'updated_at'])
    return closing


@transaction.atomic
def reopen_last_closing(user):
    latest = DailyClosing.objects.order_by('-close_date').first()
    if not latest:
        raise ValidationError('Belum ada closing yang bisa dibuka.')
    if not latest.is_locked:
        raise ValidationError('Closing terakhir sudah dalam status terbuka.')

    latest.product_snapshots.all().delete()
    latest.member_snapshots.all().delete()
    latest.delete()
    return True


def low_stock_products():
    return Product.objects.filter(reorder_point__gt=0, stock__lte=F('reorder_point')).order_by('stock')
