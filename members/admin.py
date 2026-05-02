from django.contrib import admin
from .models import Member, MemberCard, MemberWallet, MemberTopUp, MemberLedger


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'phone', 'is_active']
    search_fields = ['full_name', 'phone']


@admin.register(MemberCard)
class MemberCardAdmin(admin.ModelAdmin):
    list_display = ['card_number', 'member', 'status', 'issued_at']
    search_fields = ['card_number', 'member__full_name']
    list_filter = ['status']


@admin.register(MemberWallet)
class MemberWalletAdmin(admin.ModelAdmin):
    list_display = ['member', 'balance', 'updated_at']
    search_fields = ['member__full_name', 'member__phone']


@admin.register(MemberTopUp)
class MemberTopUpAdmin(admin.ModelAdmin):
    list_display = ['member', 'amount', 'kind', 'status', 'requested_by', 'validated_by', 'effective_at']
    search_fields = ['member__full_name', 'member__phone', 'note']
    list_filter = ['kind', 'status']


@admin.register(MemberLedger)
class MemberLedgerAdmin(admin.ModelAdmin):
    list_display = ['member', 'txn_type', 'amount', 'balance_before', 'balance_after', 'created_at']
    search_fields = ['member__full_name', 'reference_code', 'description']
    list_filter = ['txn_type']
