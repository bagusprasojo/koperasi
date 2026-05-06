from decimal import Decimal, InvalidOperation
import csv
import io
import json

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from core.decorators import role_required
from .models import Member, MemberCard, MemberLedger, MemberTopUp
from .services import (
    approve_topup,
    create_admin_topup,
    get_or_create_wallet,
    reject_topup,
    request_member_topup,
    reverse_topup,
)

User = get_user_model()


def _safe_next_url(request):
    next_url = request.GET.get('next') or request.POST.get('next') or ''
    if not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return ''
    return next_url


@role_required('admin_toko', 'pembelian', 'kasir')
def member_list(request):
    query = request.GET.get('q', '').strip()
    members = Member.objects.select_related('wallet').order_by('full_name')
    if query:
        members = members.filter(
            Q(code__icontains=query) |
            Q(full_name__icontains=query) |
            Q(phone__icontains=query)
        )
    page_obj = Paginator(members, 10).get_page(request.GET.get('page'))
    return render(request, 'members/member_list.html', {'page_obj': page_obj, 'query': query})


@role_required('admin_toko', 'pembelian')
def member_create(request):
    if request.method == 'POST':
        code = request.POST.get('code', '').strip().upper()
        full_name = request.POST.get('full_name', '').strip()
        phone = request.POST.get('phone', '').strip()
        email = request.POST.get('email', '').strip()
        address = request.POST.get('address', '').strip()
        password = request.POST.get('password', '')
        is_active = request.POST.get('is_active') == 'on'
        if not code or not full_name or not phone or not password:
            messages.error(request, 'Kode, nama, telepon, dan password wajib diisi.')
        elif Member.objects.filter(code__iexact=code).exists():
            messages.error(request, 'Kode member sudah dipakai.')
        elif Member.objects.filter(phone=phone).exists():
            messages.error(request, 'Nomor telepon sudah dipakai.')
        elif User.objects.filter(username__iexact=code).exists():
            messages.error(request, 'Kode member sudah dipakai untuk akun login.')
        else:
            try:
                validate_password(password)
                with transaction.atomic():
                    user = User.objects.create_user(
                        username=code,
                        password=password,
                        is_active=is_active,
                    )
                    member_group, _ = Group.objects.get_or_create(name='member')
                    user.groups.add(member_group)
                    member = Member.objects.create(
                        code=code,
                        user=user,
                        full_name=full_name,
                        phone=phone,
                        email=email,
                        address=address,
                        is_active=is_active,
                    )
                    get_or_create_wallet(member)
                    MemberCard.objects.create(
                        member=member,
                        card_number=code,
                        status=MemberCard.STATUS_ACTIVE,
                    )
                messages.success(request, 'Member berhasil ditambahkan dan akun login dibuat.')
                return redirect('member_list')
            except ValidationError as exc:
                messages.error(request, exc.messages[0] if exc.messages else str(exc))
            except Exception as exc:
                messages.error(request, str(exc))
    return render(request, 'members/member_create.html')


@role_required('admin_toko', 'pembelian', 'kasir')
def member_detail(request, uuid):
    member = get_object_or_404(Member.objects.select_related('wallet'), uuid=uuid)
    wallet = get_or_create_wallet(member)
    return render(request, 'members/member_detail.html', {'member': member, 'wallet': wallet})


@role_required('admin_toko', 'pembelian')
def member_edit(request, uuid):
    member = get_object_or_404(Member, uuid=uuid)
    next_url = _safe_next_url(request)
    back_url = next_url or f'/members/{member.uuid}/'
    if request.method == 'POST':
        code = (member.code or '').strip().upper()
        full_name = request.POST.get('full_name', '').strip()
        phone = request.POST.get('phone', '').strip()
        email = request.POST.get('email', '').strip()
        address = request.POST.get('address', '').strip()
        is_active = request.POST.get('is_active') == 'on'
        if not code or not full_name or not phone:
            messages.error(request, 'Kode, nama, dan nomor telepon wajib diisi.')
        elif Member.objects.exclude(id=member.id).filter(phone=phone).exists():
            messages.error(request, 'Nomor telepon sudah dipakai.')
        else:
            try:
                with transaction.atomic():
                    member.code = code
                    member.full_name = full_name
                    member.phone = phone
                    member.email = email
                    member.address = address
                    member.is_active = is_active

                    if member.user:
                        member.user.is_active = is_active
                        member.user.save(update_fields=['is_active'])
                    member.save()
                messages.success(request, 'Member berhasil diperbarui.')
                return redirect('member_list')
            except Exception as exc:
                messages.error(request, str(exc))
    return render(request, 'members/member_edit.html', {'member': member, 'next_url': next_url, 'back_url': back_url})


@role_required('admin_toko')
def member_delete(request, uuid):
    member = get_object_or_404(Member, uuid=uuid)
    if request.method == 'POST':
        try:
            name = member.full_name
            member.delete()
            messages.warning(request, f'Member "{name}" berhasil dihapus.')
        except ProtectedError:
            messages.error(
                request,
                'Member tidak bisa dihapus karena sudah memiliki transaksi. Nonaktifkan member untuk menghentikan penggunaan.',
            )
    return redirect('member_list')


@role_required('admin_toko', 'pembelian', 'kasir')
def card_list(request):
    query = request.GET.get('q', '').strip()
    cards = MemberCard.objects.select_related('member').order_by('-created_at')
    if query:
        cards = cards.filter(Q(card_number__icontains=query) | Q(member__full_name__icontains=query))
    page_obj = Paginator(cards, 10).get_page(request.GET.get('page'))
    return render(request, 'members/card_list.html', {'page_obj': page_obj, 'query': query})


@role_required('admin_toko', 'pembelian')
def card_create(request):
    members = Member.objects.filter(card__isnull=True).order_by('full_name')
    if request.method == 'POST':
        member_id = request.POST.get('member_id', '').strip()
        card_number = request.POST.get('card_number', '').strip()
        status = request.POST.get('status', MemberCard.STATUS_ACTIVE)
        if not member_id or not card_number:
            messages.error(request, 'Member dan nomor kartu wajib diisi.')
        elif MemberCard.objects.filter(card_number=card_number).exists():
            messages.error(request, 'Nomor kartu sudah dipakai.')
        else:
            member = get_object_or_404(Member, id=member_id)
            if hasattr(member, 'card'):
                messages.error(request, 'Member ini sudah punya kartu.')
            else:
                MemberCard.objects.create(member=member, card_number=card_number, status=status)
                get_or_create_wallet(member)
                messages.success(request, 'Kartu member berhasil diterbitkan.')
                return redirect('card_list')
    return render(request, 'members/card_create.html', {'members': members})


@role_required('admin_toko', 'pembelian', 'kasir')
def card_detail(request, uuid):
    card = get_object_or_404(MemberCard.objects.select_related('member', 'member__wallet'), uuid=uuid)
    wallet = get_or_create_wallet(card.member)
    return render(request, 'members/card_detail.html', {'card': card, 'wallet': wallet})


@role_required('admin_toko', 'pembelian')
def card_edit(request, uuid):
    card = get_object_or_404(MemberCard.objects.select_related('member'), uuid=uuid)
    next_url = _safe_next_url(request)
    back_url = next_url or f'/members/cards/{card.uuid}/'
    if request.method == 'POST':
        card_number = request.POST.get('card_number', '').strip()
        status = request.POST.get('status', MemberCard.STATUS_ACTIVE)
        if not card_number:
            messages.error(request, 'Nomor kartu wajib diisi.')
        elif MemberCard.objects.exclude(id=card.id).filter(card_number=card_number).exists():
            messages.error(request, 'Nomor kartu sudah dipakai.')
        else:
            card.card_number = card_number
            card.status = status
            card.save()
            messages.success(request, 'Kartu member berhasil diperbarui.')
            return redirect('card_list')
    return render(request, 'members/card_edit.html', {'card': card, 'next_url': next_url, 'back_url': back_url})


@role_required('admin_toko')
def card_delete(request, uuid):
    card = get_object_or_404(MemberCard, uuid=uuid)
    if request.method == 'POST':
        number = card.card_number
        card.delete()
        messages.warning(request, f'Kartu "{number}" berhasil dihapus.')
    return redirect('card_list')


@role_required('admin_toko')
def topup_page(request):
    query = request.GET.get('q', '').strip()
    members = Member.objects.order_by('full_name')
    if query:
        members = members.filter(Q(full_name__icontains=query) | Q(phone__icontains=query))

    confirm_data = None
    if request.method == 'POST':
        member_code = request.POST.get('member_code', '').strip().upper()
        amount_raw = request.POST.get('amount', '').strip()
        note = request.POST.get('note', '').strip()
        do_confirm = request.POST.get('do_confirm') == '1'
        try:
            if not member_code:
                raise ValueError('Kode member wajib diisi.')
            member = Member.objects.filter(code__iexact=member_code).first()
            if not member:
                raise Member.DoesNotExist
            amount = Decimal(amount_raw)
            if amount <= 0:
                raise ValueError('Nominal topup harus lebih besar dari 0.')
            if do_confirm:
                create_admin_topup(member=member, amount=amount, created_by=request.user, note=note)
                messages.success(request, 'Topup admin berhasil diproses.')
                return redirect('member_topup')
            confirm_data = {
                'member_code': member_code,
                'member_name': member.full_name,
                'member_phone': member.phone,
                'amount': amount,
                'note': note,
            }
        except Member.DoesNotExist:
            messages.error(request, 'Member tidak ditemukan. Gunakan kode member yang valid.')
        except InvalidOperation:
            messages.error(request, 'Nominal topup tidak valid.')
        except ValueError as exc:
            messages.error(request, str(exc))
        except Exception as exc:
            messages.error(request, str(exc))

    page_obj = Paginator(members, 10).get_page(request.GET.get('page'))
    return render(
        request,
        'members/topup_page.html',
        {'page_obj': page_obj, 'query': query, 'confirm_data': confirm_data},
    )


@role_required('member')
def member_topup_request(request):
    member = getattr(request.user, 'member_profile', None)
    if not member:
        messages.error(request, 'Akun ini tidak terhubung ke data member.')
        return redirect('dashboard')
    if request.method == 'POST':
        amount_raw = request.POST.get('amount', '').strip()
        note = request.POST.get('note', '').strip()
        proof_file = request.FILES.get('proof_file')
        try:
            amount = Decimal(amount_raw)
            if amount < Decimal('5000'):
                raise ValueError('Nominal topup minimal 5000.')
            if not proof_file:
                raise ValueError('Bukti transfer wajib diunggah.')
            if proof_file.size > (2 * 1024 * 1024):
                raise ValueError('Ukuran bukti transfer maksimal 2MB.')
            request_member_topup(
                member=member,
                amount=amount,
                requested_by=request.user,
                note=note,
                proof_file=proof_file,
            )
            messages.success(request, 'Request topup dikirim. Menunggu validasi admin.')
            return redirect('member_topup_request')
        except (InvalidOperation, ValueError, Exception) as exc:
            messages.error(request, str(exc))

    topups = MemberTopUp.objects.filter(member=member).order_by('-created_at')[:20]
    return render(request, 'members/member_topup_request.html', {'member': member, 'topups': topups})


@role_required('member')
def member_my_balance(request):
    member = getattr(request.user, 'member_profile', None)
    if not member:
        messages.error(request, 'Akun ini tidak terhubung ke data member.')
        return redirect('dashboard')
    wallet = get_or_create_wallet(member)
    return render(request, 'members/member_my_balance.html', {'member': member, 'wallet': wallet})


@role_required('member')
def member_my_ledger(request):
    member = getattr(request.user, 'member_profile', None)
    if not member:
        messages.error(request, 'Akun ini tidak terhubung ke data member.')
        return redirect('dashboard')
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    ledgers = MemberLedger.objects.filter(member=member).order_by('-created_at')
    if date_from:
        ledgers = ledgers.filter(created_at__date__gte=date_from)
    if date_to:
        ledgers = ledgers.filter(created_at__date__lte=date_to)
    page_obj = Paginator(ledgers, 15).get_page(request.GET.get('page'))
    return render(
        request,
        'members/member_my_ledger.html',
        {'page_obj': page_obj, 'date_from': date_from, 'date_to': date_to, 'member': member},
    )


@role_required('admin_toko')
def topup_validation_list(request):
    query = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip() or MemberTopUp.STATUS_PENDING
    topups = MemberTopUp.objects.select_related('member', 'requested_by', 'validated_by').order_by('-created_at')
    if query:
        topups = topups.filter(Q(member__full_name__icontains=query) | Q(member__phone__icontains=query))
    if status:
        topups = topups.filter(status=status)
    page_obj = Paginator(topups, 15).get_page(request.GET.get('page'))
    return render(request, 'members/topup_validation_list.html', {'page_obj': page_obj, 'query': query, 'status': status})


@role_required('admin_toko')
def topup_approve_action(request, uuid):
    topup = get_object_or_404(MemberTopUp, uuid=uuid)
    if request.method == 'POST':
        note = request.POST.get('validation_note', '').strip()
        try:
            approve_topup(topup=topup, validated_by=request.user, validation_note=note)
            messages.success(request, 'Topup berhasil di-approve.')
        except Exception as exc:
            messages.error(request, str(exc))
    return redirect('topup_validation_list')


@role_required('admin_toko')
def topup_reject_action(request, uuid):
    topup = get_object_or_404(MemberTopUp, uuid=uuid)
    if request.method == 'POST':
        note = request.POST.get('validation_note', '').strip()
        try:
            reject_topup(topup=topup, validated_by=request.user, validation_note=note)
            messages.warning(request, 'Topup ditolak.')
        except Exception as exc:
            messages.error(request, str(exc))
    return redirect('topup_validation_list')


@role_required('admin_toko')
def topup_reverse_action(request, uuid):
    topup = get_object_or_404(MemberTopUp, uuid=uuid)
    if request.method == 'POST':
        note = request.POST.get('validation_note', '').strip()
        try:
            reverse_topup(topup=topup, admin_user=request.user, note=note)
            messages.warning(request, 'Topup berhasil direversal.')
        except Exception as exc:
            messages.error(request, str(exc))
    return redirect('topup_validation_list')


@role_required('admin_toko')
def topup_bulk_admin(request):
    preview_rows = []
    can_confirm = False
    confirm_payload = '[]'

    if request.method == 'POST':
        action = request.POST.get('action', 'preview')
        if action == 'preview':
            upload = request.FILES.get('csv_file')
            if not upload:
                messages.error(request, 'File CSV wajib diunggah.')
            elif not upload.name.lower().endswith('.csv'):
                messages.error(request, 'Format file harus .csv')
            else:
                try:
                    text = io.TextIOWrapper(upload.file, encoding='utf-8-sig')
                    reader = csv.DictReader(text)
                    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
                    required = {'member_code', 'amount'}
                    if not required.issubset(set(headers)):
                        messages.error(request, 'Header CSV wajib: member_code,amount,note')
                    else:
                        seen_codes = set()
                        valid_payload = []
                        for i, row in enumerate(reader, start=2):
                            code = (row.get('member_code') or '').strip().upper()
                            amount_raw = (row.get('amount') or '').strip()
                            note = (row.get('note') or '').strip()
                            err = ''
                            member = None
                            amount = None
                            if not code:
                                err = 'Kode member kosong'
                            elif code in seen_codes:
                                err = 'Kode member duplikat dalam file bulk'
                            else:
                                member = Member.objects.filter(code__iexact=code).first()
                                if not member:
                                    err = 'Member tidak ditemukan'
                            if not err:
                                try:
                                    amount = Decimal(amount_raw)
                                    if amount <= 0:
                                        err = 'Nominal harus > 0'
                                except Exception:
                                    err = 'Nominal tidak valid'
                            item = {
                                'line_no': i,
                                'member_code': code,
                                'member_name': member.full_name if member else '-',
                                'amount_raw': amount_raw,
                                'note': note,
                                'is_valid': err == '',
                                'error': err,
                            }
                            preview_rows.append(item)
                            if not err:
                                seen_codes.add(code)
                                valid_payload.append(
                                    {
                                        'line_no': i,
                                        'member_code': code,
                                        'amount': str(amount),
                                        'note': note,
                                    }
                                )
                        if preview_rows and all(r['is_valid'] for r in preview_rows):
                            can_confirm = True
                            confirm_payload = json.dumps(valid_payload)
                        elif not preview_rows:
                            messages.error(request, 'CSV kosong atau tidak memiliki data baris.')
                        else:
                            messages.error(request, 'Ada baris tidak valid. Perbaiki file CSV lalu upload ulang.')
                except Exception as exc:
                    messages.error(request, f'Gagal membaca CSV: {exc}')
        elif action == 'confirm':
            rows_json = request.POST.get('rows_json', '[]')
            try:
                rows = json.loads(rows_json)
                if not rows:
                    raise ValueError('Data konfirmasi kosong.')
                with transaction.atomic():
                    for r in rows:
                        code = (r.get('member_code') or '').strip().upper()
                        note = (r.get('note') or '').strip()
                        try:
                            amount = Decimal(str(r.get('amount')))
                        except Exception:
                            raise ValueError(f'Baris {r.get("line_no")}: nominal tidak valid.')
                        member = Member.objects.filter(code__iexact=code).first()
                        if not member:
                            raise ValueError(f'Baris {r.get("line_no")}: member code {code} tidak ditemukan.')
                        if amount <= 0:
                            raise ValueError(f'Baris {r.get("line_no")}: nominal harus > 0.')
                        create_admin_topup(member=member, amount=amount, created_by=request.user, note=note)
                messages.success(request, f'Bulk topup berhasil diproses: {len(rows)} baris.')
                return redirect('topup_bulk_admin')
            except Exception as exc:
                messages.error(request, f'Bulk topup gagal: {exc}')

    return render(
        request,
        'members/topup_bulk_admin.html',
        {
            'preview_rows': preview_rows,
            'can_confirm': can_confirm,
            'confirm_payload': confirm_payload,
        },
    )


@role_required('admin_toko')
def topup_bulk_template_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="template_topup_bulk.csv"'
    writer = csv.writer(response)
    writer.writerow(['member_code', 'amount', 'note'])
    writer.writerow(['MBR001', '50000', 'Topup promo'])
    writer.writerow(['MBR002', '25000', 'Topup reguler'])
    return response


@role_required('admin_toko', 'pembelian', 'kasir')
def ledger_list(request):
    query = request.GET.get('q', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    ledgers = MemberLedger.objects.select_related('member', 'card').order_by('-created_at')
    if query:
        ledgers = ledgers.filter(
            Q(member__full_name__icontains=query) |
            Q(member__phone__icontains=query) |
            Q(card__card_number__icontains=query) |
            Q(reference_code__icontains=query)
        )
    if date_from:
        ledgers = ledgers.filter(created_at__date__gte=date_from)
    if date_to:
        ledgers = ledgers.filter(created_at__date__lte=date_to)
    page_obj = Paginator(ledgers, 15).get_page(request.GET.get('page'))
    return render(
        request,
        'members/ledger_list.html',
        {'page_obj': page_obj, 'query': query, 'date_from': date_from, 'date_to': date_to},
    )
