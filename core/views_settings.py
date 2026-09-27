from django.contrib import messages
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods

from core.constants import Role
from core.decorators import role_required
from core.models import StoreSetting


@role_required(Role.ADMIN_TOKO, perm='manage_settings')
@require_http_methods(['GET', 'POST'])
def store_settings_view(request):
    setting = StoreSetting.get_settings()

    if request.method == 'POST':
        setting.store_name = request.POST.get('store_name', '').strip() or 'Koperasi'
        setting.store_address = request.POST.get('store_address', '').strip()
        setting.store_phone = request.POST.get('store_phone', '').strip()
        setting.receipt_footer = request.POST.get('receipt_footer', '').strip() or 'Terima Kasih Atas Kunjungan Anda'
        setting.pos_allow_negative_stock = request.POST.get('pos_allow_negative_stock') == 'on'
        setting.save()

        messages.success(request, 'Pengaturan toko & kebijakan POS berhasil disimpan.')
        return redirect('store_settings')

    return render(
        request,
        'core/store_settings.html',
        {
            'setting': setting,
        },
    )
