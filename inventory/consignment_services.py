import re
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone

from members.models import Member
from members.services import create_admin_topup

from .models import (
    Category,
    ConsignmentBatch,
    ConsignmentBatchItem,
    Consignor,
    DailyClosing,
    InventoryTransaction,
    InventoryTransactionItem,
    Product,
    ProductPriceTier,
    StockLedger,
    Unit,
)
from .services import _ensure_not_closed, _rebuild_product_stock_from_ledgers, _tx_number


def generate_consignor_code(name: str = '') -> str:
    """
    Generate kode unik penitip seperti P01, P02, dst atau berbasis inisial nama.
    """
    cleaned_name = re.sub(r'[^A-Za-z0-9]', '', (name or '').upper())
    prefix = cleaned_name[:4] if len(cleaned_name) >= 3 else 'P'
    
    count = Consignor.objects.filter(code__startswith=prefix).count()
    code = f"{prefix}{count + 1:02d}"
    
    while Consignor.objects.filter(code=code).exists():
        count += 1
        code = f"{prefix}{count:02d}"
        
    return code


def generate_consignment_product_sku(consignor: Consignor) -> str:
    """
    Generate SKU unik untuk barang titipan dengan format: TP-{consignor.code}-{seq:03d}
    Contoh: TP-P01-001, TP-P01-002
    """
    safe_code = re.sub(r'[^A-Za-z0-9_-]', '', consignor.code.upper())
    prefix = f"TP-{safe_code}"
    
    # Ambil nomor urut tertinggi untuk penitip ini
    existing_skus = Product.objects.filter(sku__startswith=prefix).values_list('sku', flat=True)
    max_seq = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    for s in existing_skus:
        match = pattern.match(s)
        if match:
            try:
                seq = int(match.group(1))
                if seq > max_seq:
                    max_seq = seq
            except ValueError:
                pass
                
    new_seq = max_seq + 1
    sku = f"{prefix}-{new_seq:03d}"
    
    while Product.objects.filter(sku=sku).exists():
        new_seq += 1
        sku = f"{prefix}-{new_seq:03d}"
        
    return sku


def generate_batch_number(consignor: Consignor, batch_date: date) -> str:
    date_str = batch_date.strftime('%Y%m%d')
    safe_code = re.sub(r'[^A-Za-z0-9]', '', consignor.code.upper())[:6]
    uid = uuid4().hex[:4].upper()
    return f"CSG-{date_str}-{safe_code}-{uid}"


@transaction.atomic
def create_or_update_consignor(
    *,
    consignor_id=None,
    code: str = '',
    name: str,
    phone: str = '',
    address: str = '',
    member_id=None,
    bank_name: str = '',
    bank_account: str = '',
    bank_account_holder: str = '',
    is_active: bool = True,
    notes: str = '',
    user=None,
) -> Consignor:
    name = (name or '').strip()
    if not name:
        raise ValidationError('Nama penitip wajib diisi.')
        
    code = (code or '').strip().upper()
    if not code:
        code = generate_consignor_code(name)
        
    member = None
    if member_id:
        member = Member.objects.filter(id=member_id, is_active=True).first()
        if not member:
            raise ValidationError('Anggota koperasi yang dipilih tidak valid atau tidak aktif.')
            
    if consignor_id:
        consignor = Consignor.objects.select_for_update().filter(id=consignor_id).first()
        if not consignor:
            raise ValidationError('Data penitip tidak ditemukan.')
            
        if Consignor.objects.filter(code=code).exclude(id=consignor.id).exists():
            raise ValidationError(f"Kode penitip '{code}' sudah digunakan penitip lain.")
            
        consignor.code = code
        consignor.name = name
        consignor.phone = phone
        consignor.address = address
        consignor.member = member
        consignor.bank_name = bank_name
        consignor.bank_account = bank_account
        consignor.bank_account_holder = bank_account_holder
        consignor.is_active = is_active
        consignor.notes = notes
        consignor.updated_by = user
        consignor.save()
        return consignor
        
    if Consignor.objects.filter(code=code).exists():
        raise ValidationError(f"Kode penitip '{code}' sudah digunakan.")
        
    consignor = Consignor.objects.create(
        code=code,
        name=name,
        phone=phone,
        address=address,
        member=member,
        bank_name=bank_name,
        bank_account=bank_account,
        bank_account_holder=bank_account_holder,
        is_active=is_active,
        notes=notes,
        created_by=user,
        updated_by=user,
    )
    return consignor


@transaction.atomic
def create_consignment_product(
    *,
    consignor: Consignor,
    name: str,
    category_id,
    unit_id=None,
    cost_price: Decimal,
    sale_price: Decimal,
    sku: str = '',
    barcode: str = '',
    allow_decimal_qty: bool = False,
    reorder_point: Decimal = Decimal('0.000'),
    user=None,
) -> Product:
    """
    Mendaftarkan produk titipan baru yang langsung terhubung ke penitip bersangkutan.
    HPP diatur ke cost_price penitip dan tier harga level 1 ke sale_price.
    """
    name = (name or '').strip()
    if not name:
        raise ValidationError('Nama barang titipan wajib diisi.')
        
    category = Category.objects.filter(id=category_id).first()
    if not category:
        raise ValidationError('Kategori barang wajib dipilih.')
        
    unit = Unit.objects.filter(id=unit_id).first() if unit_id else None
    
    cost_price = Decimal(str(cost_price)).quantize(Decimal('0.01'))
    sale_price = Decimal(str(sale_price)).quantize(Decimal('0.01'))
    
    if cost_price <= Decimal('0'):
        raise ValidationError('Harga titip / HPP harus lebih besar dari 0.')
    if sale_price < cost_price:
        raise ValidationError('Harga jual koperasi tidak boleh lebih kecil dari harga titip.')
        
    sku = (sku or '').strip().upper()
    if not sku:
        sku = generate_consignment_product_sku(consignor)
    elif Product.objects.filter(sku=sku).exists():
        raise ValidationError(f"SKU '{sku}' sudah digunakan produk lain.")
        
    barcode = (barcode or '').strip() or None
    if barcode and Product.objects.filter(barcode=barcode).exists():
        raise ValidationError(f"Barcode '{barcode}' sudah digunakan produk lain.")
        
    product = Product.objects.create(
        category=category,
        unit=unit,
        name=name,
        sku=sku,
        barcode=barcode,
        stock=Decimal('0.000'),
        last_purchase_price=cost_price,
        cost_of_goods_sold=cost_price,
        reorder_point=reorder_point,
        allow_decimal_qty=allow_decimal_qty,
        is_consignment=True,
        consignor=consignor,
    )
    
    # Buat Level 1 Price Tier default
    ProductPriceTier.objects.create(
        product=product,
        level=1,
        min_qty=Decimal('1.000'),
        max_qty=Decimal('999999.000'),
        price=sale_price,
        source_mode='final',
    )
    
    return product


@transaction.atomic
def record_consignment_inflow(
    *,
    consignor: Consignor,
    batch_date: date,
    items_data: list,
    user,
    notes: str = '',
) -> ConsignmentBatch:
    """
    Mencatat penerimaan barang titipan di pagi hari:
    1. Validasi items & tanggal (tidak boleh sudah tutup harian).
    2. Membuat ConsignmentBatch (status OPEN).
    3. Membuat InventoryTransaction (tipe consignment_in).
    4. Menambah stok produk melalui StockLedger.
    """
    _ensure_not_closed(batch_date)
    
    if not items_data:
        raise ValidationError('Daftar barang titipan masuk tidak boleh kosong.')
        
    batch_number = generate_batch_number(consignor, batch_date)
    
    tx = InventoryTransaction.objects.create(
        tx_number=_tx_number('CSG-IN'),
        tx_type=InventoryTransaction.TYPE_CONSIGNMENT_IN,
        tx_date=batch_date,
        consignor=consignor,
        reference=batch_number,
        note=notes or f"Titipan Pagi: {consignor.name}",
        created_by=user,
    )
    
    batch = ConsignmentBatch.objects.create(
        batch_number=batch_number,
        consignor=consignor,
        batch_date=batch_date,
        status=ConsignmentBatch.STATUS_OPEN,
        inflow_transaction=tx,
        notes=notes,
        created_by=user,
    )
    
    total_received_amount = Decimal('0.00')
    affected_products = set()
    
    for row in items_data:
        product = row['product']
        affected_products.add(product.id)
        
        try:
            qty = Decimal(str(row['qty'])).quantize(Decimal('0.001'))
            cost_price = Decimal(str(row.get('cost_price', product.cost_of_goods_sold))).quantize(Decimal('0.01'))
            sale_price = Decimal(str(row.get('sale_price', Decimal('0.00')))).quantize(Decimal('0.01'))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError(f"Format kuantitas atau harga tidak valid untuk produk '{product.name}'.")
            
        if qty <= Decimal('0'):
            raise ValidationError(f"Qty masuk untuk '{product.name}' harus lebih besar dari 0.")
        if cost_price <= Decimal('0'):
            raise ValidationError(f"Harga titip (HPP) untuk '{product.name}' harus lebih besar dari 0.")
            
        # Jika sale_price tidak dispesifikasi, ambil dari price tier level 1
        if sale_price <= Decimal('0'):
            tier = product.price_tiers.order_by('level').first()
            sale_price = tier.price if tier else cost_price
            
        if sale_price < cost_price:
            raise ValidationError(f"Harga jual '{product.name}' ({sale_price}) tidak boleh lebih kecil dari HPP ({cost_price}).")
            
        # Update HPP produk dan level 1 tier harga jika ada perubahan kesepakatan harga
        product.cost_of_goods_sold = cost_price
        product.last_purchase_price = cost_price
        product.save(update_fields=['cost_of_goods_sold', 'last_purchase_price', 'updated_at'])
        
        first_tier = product.price_tiers.order_by('level').first()
        if first_tier and first_tier.price != sale_price:
            first_tier.price = sale_price
            first_tier.save(update_fields=['price', 'updated_at'])
            
        line_received_val = (cost_price * qty).quantize(Decimal('0.01'))
        total_received_amount += line_received_val
        
        ConsignmentBatchItem.objects.create(
            batch=batch,
            product=product,
            cost_price=cost_price,
            sale_price=sale_price,
            qty_received=qty,
            qty_sold=Decimal('0.000'),
            qty_returned=Decimal('0.000'),
            qty_loss=Decimal('0.000'),
            payable_amount=Decimal('0.00'),
            coop_margin=Decimal('0.00'),
            notes=row.get('notes', ''),
        )
        
        InventoryTransactionItem.objects.create(
            transaction=tx,
            product=product,
            qty=qty,
            unit_cost=cost_price,
            total_cost=line_received_val,
        )
        
        before = product.stock
        after = before + qty
        StockLedger.objects.create(
            product=product,
            tx=tx,
            tx_date=batch_date,
            qty_in=qty,
            qty_out=Decimal('0.000'),
            balance_before=before,
            balance_after=after,
            unit_cost_at_txn=cost_price,
            value_in=line_received_val,
            value_out=Decimal('0.00'),
            note=f"Titipan Pagi - Batch {batch.batch_number}",
        )
        
    for p_id in affected_products:
        p = Product.objects.get(id=p_id)
        _rebuild_product_stock_from_ledgers(p)
        
    tx.total_amount = total_received_amount
    tx.save(update_fields=['total_amount', 'updated_at'])
    
    batch.total_received_amount = total_received_amount
    batch.save(update_fields=['total_received_amount', 'updated_at'])
    
    return batch


def get_consignment_settlement_preview(batch: ConsignmentBatch) -> dict:
    """
    Menghitung rekapitulasi real-time untuk batch titipan sore hari:
    - Qty Masuk Pagi
    - Qty Terjual POS (berdasarkan mutasi kasir sejak batch dibuat)
    - Sisa Stok Aktual di rak toko
    - Estimasi Nilai Bayar ke Penitip & Margin Koperasi
    """
    from sales.models import SaleItem

    items_summary = []
    total_received_val = Decimal('0.00')
    total_estimated_payable = Decimal('0.00')
    total_estimated_retail = Decimal('0.00')
    total_estimated_margin = Decimal('0.00')
    
    batch_items = batch.items.select_related('product', 'product__unit').order_by('id')
    
    for item in batch_items:
        product = item.product
        
        # Hitung penjualan POS sejak batch inflow dibuat
        pos_sold = Decimal('0.000')
        sales_agg = SaleItem.objects.filter(
            product=product,
            sale__created_at__gte=batch.created_at,
        ).aggregate(total_qty=Sum('qty'))
        
        if sales_agg['total_qty'] is not None:
            pos_sold = Decimal(str(sales_agg['total_qty']))
            
        current_stock = product.stock
        
        # Jika batch masih OPEN, default sisa retur adalah max(0, product.stock)
        # dan default qty_sold adalah min(item.qty_received, pos_sold atau item.qty_received - current_stock)
        if batch.status == ConsignmentBatch.STATUS_OPEN:
            expected_returned = current_stock if current_stock >= Decimal('0') else Decimal('0.000')
            # Jika stok habis atau minus karena penjualan:
            if expected_returned > item.qty_received:
                expected_returned = item.qty_received
                
            calc_sold = item.qty_received - expected_returned
            if calc_sold < Decimal('0'):
                calc_sold = Decimal('0.000')
                
            payable = (calc_sold * item.cost_price).quantize(Decimal('0.01'))
            retail = (calc_sold * item.sale_price).quantize(Decimal('0.01'))
            margin = (retail - payable).quantize(Decimal('0.01'))
            
            items_summary.append({
                'item_id': item.id,
                'product_id': product.id,
                'product_name': product.name,
                'product_sku': product.sku,
                'unit_name': product.unit.name if product.unit else 'pcs',
                'cost_price': item.cost_price,
                'sale_price': item.sale_price,
                'qty_received': item.qty_received,
                'pos_sold': pos_sold,
                'current_stock': current_stock,
                'suggested_returned': expected_returned,
                'suggested_sold': calc_sold,
                'suggested_loss': Decimal('0.000'),
                'payable_amount': payable,
                'coop_margin': margin,
                'retail_amount': retail,
            })
            total_estimated_payable += payable
            total_estimated_retail += retail
            total_estimated_margin += margin
        else:
            # Sudah settled
            retail = (item.qty_sold * item.sale_price).quantize(Decimal('0.01'))
            items_summary.append({
                'item_id': item.id,
                'product_id': product.id,
                'product_name': product.name,
                'product_sku': product.sku,
                'unit_name': product.unit.name if product.unit else 'pcs',
                'cost_price': item.cost_price,
                'sale_price': item.sale_price,
                'qty_received': item.qty_received,
                'pos_sold': item.qty_sold,
                'current_stock': current_stock,
                'suggested_returned': item.qty_returned,
                'suggested_sold': item.qty_sold,
                'suggested_loss': item.qty_loss,
                'payable_amount': item.payable_amount,
                'coop_margin': item.coop_margin,
                'retail_amount': retail,
            })
            total_estimated_payable += item.payable_amount
            total_estimated_retail += retail
            total_estimated_margin += item.coop_margin
            
        total_received_val += (item.qty_received * item.cost_price).quantize(Decimal('0.01'))
        
    return {
        'batch': batch,
        'items': items_summary,
        'total_received_val': total_received_val,
        'total_payable': total_estimated_payable,
        'total_retail': total_estimated_retail,
        'total_margin': total_estimated_margin,
    }


@transaction.atomic
def settle_consignment_batch(
    *,
    batch: ConsignmentBatch,
    items_settlement: list,
    payout_method: str,
    user,
    payout_reference: str = '',
    notes: str = '',
    request=None,
) -> ConsignmentBatch:
    """
    Memproses pelunasan barang titipan sore hari:
    1. Validasi status OPEN & tanggal closing.
    2. Update qty_sold, qty_returned, qty_loss pada tiap item.
    3. Buat InventoryTransaction (consignment_return) untuk mengosongkan sisa stok ke 0.
    4. Bayarkan hak penitip (Tunai / Transfer / Dompet Saldo Anggota).
    5. Tandai batch sebagai STATUS_SETTLED.
    """
    _ensure_not_closed(date.today())
    
    batch = ConsignmentBatch.objects.select_for_update().get(id=batch.id)
    if batch.status != ConsignmentBatch.STATUS_OPEN:
        raise ValidationError('Batch titipan ini tidak berstatus aktif atau sudah pernah diselesaikan.')
        
    valid_methods = [
        ConsignmentBatch.PAYOUT_METHOD_CASH,
        ConsignmentBatch.PAYOUT_METHOD_MEMBER_DEPOSIT,
        ConsignmentBatch.PAYOUT_METHOD_TRANSFER,
    ]
    if payout_method not in valid_methods:
        raise ValidationError('Metode pembayaran ke penitip tidak valid.')
        
    if payout_method == ConsignmentBatch.PAYOUT_METHOD_MEMBER_DEPOSIT:
        if not batch.consignor.member:
            raise ValidationError(
                f"Penitip '{batch.consignor.name}' tidak terhubung ke akun anggota koperasi. "
                "Pilih metode Tunai atau Transfer Bank, atau tautkan penitip ke akun anggota terlebih dahulu."
            )
        if not batch.consignor.member.is_active:
            raise ValidationError(f"Anggota '{batch.consignor.member.full_name}' saat ini tidak aktif.")
            
    items_dict = {str(item.id): item for item in batch.items.select_related('product').all()}
    
    total_sold_cost = Decimal('0.00')
    total_sold_retail = Decimal('0.00')
    total_coop_margin = Decimal('0.00')
    total_return_amount = Decimal('0.00')
    
    return_tx_items = []
    affected_products = set()
    
    for row in items_settlement:
        item_id = str(row.get('item_id', ''))
        batch_item = items_dict.get(item_id)
        if not batch_item:
            raise ValidationError(f"Item titipan ID {item_id} tidak valid.")
            
        product = batch_item.product
        affected_products.add(product.id)
        
        try:
            qty_returned = Decimal(str(row.get('qty_returned', '0'))).quantize(Decimal('0.001'))
            qty_loss = Decimal(str(row.get('qty_loss', '0'))).quantize(Decimal('0.001'))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError(f"Format kuantitas retur/loss tidak valid untuk '{product.name}'.")
            
        if qty_returned < Decimal('0') or qty_loss < Decimal('0'):
            raise ValidationError(f"Jumlah retur atau rusak/hilang '{product.name}' tidak boleh negatif.")
            
        if (qty_returned + qty_loss) > batch_item.qty_received:
            raise ValidationError(
                f"Jumlah retur ({qty_returned}) + rusak ({qty_loss}) untuk '{product.name}' "
                f"melebihi jumlah titip pagi ({batch_item.qty_received})."
            )
            
        qty_sold = batch_item.qty_received - qty_returned - qty_loss
        if qty_sold < Decimal('0'):
            qty_sold = Decimal('0.000')
            
        payable = (qty_sold * batch_item.cost_price).quantize(Decimal('0.01'))
        retail = (qty_sold * batch_item.sale_price).quantize(Decimal('0.01'))
        margin = (retail - payable).quantize(Decimal('0.01'))
        
        batch_item.qty_sold = qty_sold
        batch_item.qty_returned = qty_returned
        batch_item.qty_loss = qty_loss
        batch_item.payable_amount = payable
        batch_item.coop_margin = margin
        batch_item.save(update_fields=['qty_sold', 'qty_returned', 'qty_loss', 'payable_amount', 'coop_margin', 'updated_at'])
        
        total_sold_cost += payable
        total_sold_retail += retail
        total_coop_margin += margin
        
        # Sisa barang yang tidak laku (retur + loss) harus dikeluarkan dari stok sistem
        # agar stok toko kembali ke 0 untuk malam hari.
        remaining_stock_to_clear = qty_returned + qty_loss
        if remaining_stock_to_clear > Decimal('0'):
            return_val = (remaining_stock_to_clear * batch_item.cost_price).quantize(Decimal('0.01'))
            total_return_amount += return_val
            return_tx_items.append({
                'product': product,
                'qty': remaining_stock_to_clear,
                'cost_price': batch_item.cost_price,
                'total_cost': return_val,
                'notes': f"Retur: {qty_returned}, Rusak/Basi: {qty_loss}",
            })
            
    # Buat InventoryTransaction return jika ada sisa barang
    if return_tx_items:
        tx_return = InventoryTransaction.objects.create(
            tx_number=_tx_number('CSG-RET'),
            tx_type=InventoryTransaction.TYPE_CONSIGNMENT_RETURN,
            tx_date=date.today(),
            consignor=batch.consignor,
            reference=batch.batch_number,
            total_amount=total_return_amount,
            note=f"Pengembalian & Penyesuaian Sisa Titipan Sore: {batch.batch_number}",
            created_by=user,
        )
        for ret_row in return_tx_items:
            p = ret_row['product']
            q = ret_row['qty']
            c = ret_row['cost_price']
            val = ret_row['total_cost']
            
            InventoryTransactionItem.objects.create(
                transaction=tx_return,
                product=p,
                qty=-q,
                unit_cost=c,
                total_cost=val,
            )
            
            before = p.stock
            after = before - q
            StockLedger.objects.create(
                product=p,
                tx=tx_return,
                tx_date=date.today(),
                qty_in=Decimal('0.000'),
                qty_out=q,
                balance_before=before,
                balance_after=after,
                unit_cost_at_txn=c,
                value_in=Decimal('0.00'),
                value_out=val,
                note=f"Retur Sore Titipan - Batch {batch.batch_number}",
            )
            
        batch.return_transaction = tx_return
        
    for p_id in affected_products:
        p = Product.objects.get(id=p_id)
        _rebuild_product_stock_from_ledgers(p)
        
    # Proses pencairan dana ke dompet anggota jika metode member_deposit
    if payout_method == ConsignmentBatch.PAYOUT_METHOD_MEMBER_DEPOSIT and total_sold_cost > Decimal('0'):
        audit_ctx = {}
        if request:
            from members.services import build_audit_context
            audit_ctx = build_audit_context(request)
            
        create_admin_topup(
            member=batch.consignor.member,
            amount=total_sold_cost,
            created_by=user,
            note=f"Hasil penjualan titipan batch {batch.batch_number}",
            audit_context=audit_ctx,
        )
        
    batch.status = ConsignmentBatch.STATUS_SETTLED
    batch.settled_at = timezone.now()
    batch.settled_by = user
    batch.payout_method = payout_method
    batch.payout_reference = payout_reference
    batch.total_sold_cost = total_sold_cost
    batch.total_sold_retail = total_sold_retail
    batch.total_coop_margin = total_coop_margin
    batch.notes = (batch.notes + "\n" + notes).strip() if notes else batch.notes
    batch.save()
    
    return batch


@transaction.atomic
def cancel_consignment_batch(batch: ConsignmentBatch, user, reason: str = '') -> ConsignmentBatch:
    """
    Membatalkan batch titipan yang belum diselesaikan (status OPEN),
    dan mengembalikan stok awal ke 0.
    """
    _ensure_not_closed(batch.batch_date)
    
    batch = ConsignmentBatch.objects.select_for_update().get(id=batch.id)
    if batch.status != ConsignmentBatch.STATUS_OPEN:
        raise ValidationError('Hanya batch berstatus aktif yang bisa dibatalkan.')
        
    # Batalkan transaksi inflow
    if batch.inflow_transaction:
        inflow_tx = batch.inflow_transaction
        affected_products = set()
        
        # Buat transaksi reversal
        tx_cancel = InventoryTransaction.objects.create(
            tx_number=_tx_number('CSG-CNL'),
            tx_type=InventoryTransaction.TYPE_CONSIGNMENT_RETURN,
            tx_date=date.today(),
            consignor=batch.consignor,
            reference=batch.batch_number,
            total_amount=batch.total_received_amount,
            note=f"Pembatalan Batch Titipan {batch.batch_number}: {reason}",
            created_by=user,
        )
        
        for item in batch.items.select_related('product').all():
            p = item.product
            affected_products.add(p.id)
            q = item.qty_received
            c = item.cost_price
            val = (q * c).quantize(Decimal('0.01'))
            
            InventoryTransactionItem.objects.create(
                transaction=tx_cancel,
                product=p,
                qty=-q,
                unit_cost=c,
                total_cost=val,
            )
            
            before = p.stock
            after = before - q
            StockLedger.objects.create(
                product=p,
                tx=tx_cancel,
                tx_date=date.today(),
                qty_in=Decimal('0.000'),
                qty_out=q,
                balance_before=before,
                balance_after=after,
                unit_cost_at_txn=c,
                value_in=Decimal('0.00'),
                value_out=val,
                note=f"Batal Titipan - Batch {batch.batch_number}",
            )
            
        for p_id in affected_products:
            p = Product.objects.get(id=p_id)
            _rebuild_product_stock_from_ledgers(p)
            
        batch.return_transaction = tx_cancel
        
    batch.status = ConsignmentBatch.STATUS_CANCELLED
    batch.notes = (batch.notes + f"\n[DIBATALKAN] {reason} oleh {user.username if user else 'system'}").strip()
    batch.save(update_fields=['status', 'notes', 'return_transaction', 'updated_at'])
    
    return batch
