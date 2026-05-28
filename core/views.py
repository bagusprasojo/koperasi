from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.shortcuts import redirect, render
from django.db.models import Sum, F


def home(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return redirect('login')


@login_required
def dashboard(request):
    user_roles = list(request.user.groups.values_list('name', flat=True))
    topup_stats = None
    member_dashboard = None
    admin_dashboard = None
    if 'admin_toko' in user_roles:
        from inventory.models import Product, Supplier, InventoryTransaction, DailyClosing
        from members.models import MemberTopUp
        from members.models import Member
        from sales.models import Sale
        today = timezone.localdate()
        topup_stats = {
            'pending': MemberTopUp.objects.filter(status=MemberTopUp.STATUS_PENDING).count(),
            'approved_today': MemberTopUp.objects.filter(
                status=MemberTopUp.STATUS_APPROVED,
                validated_at__date=today,
            ).count(),
            'rejected_today': MemberTopUp.objects.filter(
                status=MemberTopUp.STATUS_REJECTED,
                validated_at__date=today,
            ).count(),
        }
        sales_today_qs = Sale.objects.filter(created_at__date=today)
        purchase_today_qs = InventoryTransaction.objects.filter(
            tx_type=InventoryTransaction.TYPE_PURCHASE,
            tx_date=today,
        )
        low_stock_count = Product.objects.filter(reorder_point__gt=0, stock__lte=F('reorder_point')).count()
        closing_today = DailyClosing.objects.filter(close_date=today).first()
        admin_dashboard = {
            'today': today,
            'profile': {
                'username': request.user.username,
                'full_name': request.user.get_full_name() or '-',
                'email': request.user.email or '-',
                'last_login': request.user.last_login,
                'roles': user_roles,
            },
            'kpi': {
                'pending_topup': topup_stats['pending'],
                'approved_topup_today': topup_stats['approved_today'],
                'rejected_topup_today': topup_stats['rejected_today'],
                'sales_today_count': sales_today_qs.count(),
                'sales_today_total': sales_today_qs.aggregate(v=Sum('total'))['v'] or 0,
                'purchase_today_count': purchase_today_qs.count(),
                'purchase_today_total': purchase_today_qs.aggregate(v=Sum('total_amount'))['v'] or 0,
                'low_stock_count': low_stock_count,
                'product_count': Product.objects.count(),
                'supplier_count': Supplier.objects.count(),
                'member_active_count': Member.objects.filter(is_active=True).count(),
                'member_total_count': Member.objects.count(),
                'closing_today_done': bool(closing_today),
            },
        }
    if 'member' in user_roles:
        from members.models import MemberTopUp
        from members.services import get_or_create_wallet
        from sales.models import Sale, SaleItem

        member = getattr(request.user, 'member_profile', None)
        if member:
            wallet = get_or_create_wallet(member)
            today = timezone.localdate()
            month_start = today.replace(day=1)
            sales_qs = Sale.objects.filter(
                member=member,
                created_at__date__gte=month_start,
                created_at__date__lte=today,
            )
            top_products = list(
                SaleItem.objects.filter(sale__member=member)
                .values('product__name')
                .annotate(total_qty=Sum('qty'), total_amount=Sum('line_total'))
                .order_by('-total_qty', '-total_amount')[:5]
            )
            recent_topups = list(
                MemberTopUp.objects.filter(member=member)
                .order_by('-created_at')[:5]
            )
            pending_topups = list(
                MemberTopUp.objects.filter(member=member, status=MemberTopUp.STATUS_PENDING)
                .order_by('-created_at')[:5]
            )
            member_dashboard = {
                'member': member,
                'wallet': wallet,
                'period': {'date_from': month_start, 'date_to': today},
                'summary': {
                    'transaction_count': sales_qs.count(),
                    'total_spent': sales_qs.aggregate(v=Sum('total'))['v'] or 0,
                    'topup_count': MemberTopUp.objects.filter(
                        member=member,
                        status=MemberTopUp.STATUS_APPROVED,
                        effective_at__date__gte=month_start,
                        effective_at__date__lte=today,
                    ).count(),
                    'topup_total': MemberTopUp.objects.filter(
                        member=member,
                        status=MemberTopUp.STATUS_APPROVED,
                        effective_at__date__gte=month_start,
                        effective_at__date__lte=today,
                    ).aggregate(v=Sum('amount'))['v'] or 0,
                },
                'top_products': top_products,
                'recent_topups': recent_topups,
                'pending_topups': pending_topups,
            }
    return render(
        request,
        'core/dashboard.html',
        {
            'user_roles': user_roles,
            'topup_stats': topup_stats,
            'member_dashboard': member_dashboard,
            'admin_dashboard': admin_dashboard,
        },
    )
