from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render

from inventory.models import Product
from inventory.services import post_pos_sale
from core.decorators import role_required
from members.models import MemberCard
from members.services import charge_member_by_card

from .models import Sale, SaleItem, SalePayment


@role_required('kasir', 'admin_toko')
def pos_page(request):
    products = Product.objects.prefetch_related('price_tiers').order_by('name')
    if request.method == 'POST':
        try:
            product_id = request.POST.get('product_id', '').strip()
            qty = int(request.POST.get('qty', '1'))
            method = request.POST.get('method', SalePayment.METHOD_CASH)
            card_number = request.POST.get('card_number', '').strip()
            if qty <= 0:
                raise ValueError
            product = Product.objects.get(id=product_id)
            tier1 = product.price_tiers.filter(level=1).first()
            if not tier1:
                messages.error(request, 'Produk belum memiliki harga level 1.')
                return redirect('pos_page')

            unit_price = tier1.price
            total = (unit_price * Decimal(qty)).quantize(Decimal('0.01'))
            with transaction.atomic():
                sale = Sale.objects.create(
                    sale_number=f'SL-{uuid4().hex[:10].upper()}',
                    subtotal=total,
                    total=total,
                    created_by=request.user,
                )
                SaleItem.objects.create(
                    sale=sale,
                    product=product,
                    qty=qty,
                    unit_price=unit_price,
                    line_total=total,
                )
                if method == SalePayment.METHOD_MEMBER:
                    if not card_number:
                        messages.error(request, 'Nomor kartu member wajib diisi untuk metode member deposit.')
                        raise InvalidOperation
                    ledger = charge_member_by_card(
                        card_number=card_number,
                        amount=total,
                        reference_code=sale.sale_number,
                        description='Pembayaran POS',
                    )
                    sale.member = ledger.member
                    sale.save(update_fields=['member', 'updated_at'])
                    SalePayment.objects.create(
                        sale=sale,
                        method=method,
                        amount=total,
                        reference=card_number,
                    )
                else:
                    SalePayment.objects.create(sale=sale, method=method, amount=total)

            post_pos_sale(product=product, qty=qty, user=request.user, reference=sale.sale_number, note='Checkout POS')
            messages.success(request, f'Transaksi {sale.sale_number} berhasil diproses.')
            return redirect('pos_page')
        except Product.DoesNotExist:
            messages.error(request, 'Produk tidak valid.')
        except MemberCard.DoesNotExist:
            messages.error(request, 'Kartu member tidak ditemukan.')
        except (InvalidOperation, ValueError) as exc:
            if str(exc):
                messages.error(request, str(exc))
        except Exception as exc:
            messages.error(request, str(exc))

    return render(request, 'sales/pos_page.html', {'products': products})
