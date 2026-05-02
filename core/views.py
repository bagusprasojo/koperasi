from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.shortcuts import redirect, render


def home(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return redirect('login')


@login_required
def dashboard(request):
    user_roles = list(request.user.groups.values_list('name', flat=True))
    topup_stats = None
    if 'admin_toko' in user_roles:
        from members.models import MemberTopUp
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
    return render(
        request,
        'core/dashboard.html',
        {'user_roles': user_roles, 'topup_stats': topup_stats},
    )
