from django.contrib import admin
from .models import LoginSecurityRecord, StoreSetting


@admin.register(LoginSecurityRecord)
class LoginSecurityRecordAdmin(admin.ModelAdmin):
    list_display = ('identifier', 'failed_count', 'last_attempt_at', 'locked_until', 'is_locked_status')
    search_fields = ('identifier',)
    readonly_fields = ('identifier', 'failed_count', 'last_attempt_at')
    actions = ['unlock_selected_records']

    @admin.display(description='Status Terkunci', boolean=True)
    def is_locked_status(self, obj):
        from django.utils import timezone
        return bool(obj.locked_until and obj.locked_until > timezone.now())

    @admin.action(description='Buka Kunci (Unlock) Akun/IP yang Terpilih')
    def unlock_selected_records(self, request, queryset):
        count = queryset.update(failed_count=0, locked_until=None)
        self.message_user(request, f"{count} akun/IP berhasil dibuka kuncinya (unlocked).")


@admin.register(StoreSetting)
class StoreSettingAdmin(admin.ModelAdmin):
    list_display = ('store_name', 'store_phone', 'pos_allow_negative_stock', 'updated_at')

