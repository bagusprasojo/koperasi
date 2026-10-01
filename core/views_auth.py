import logging
import math
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.utils import timezone

from .models import LoginSecurityRecord

logger = logging.getLogger('security.auth')


def get_client_ip(request) -> str:
    """Mengambil IP address asli klien, mempertimbangkan header reverse proxy."""
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or '127.0.0.1'


class RateLimitedLoginView(auth_views.LoginView):
    """
    Subclass LoginView dengan proteksi anti brute-force dan rate-limiting terpusat
    menggunakan model database LoginSecurityRecord:
    - Tersinkronisasi 100% di semua worker Gunicorn, proses server, dan multi-server.
    - Melacak percobaan gagal berdasarkan kombinasi Alamat IP dan Username.
    - Mengunci login selama LOGIN_LOCKOUT_DURATION detik jika gagal mencapai LOGIN_MAX_FAILED_ATTEMPTS kali.
    - Mencatat log audit keamanan saat terjadi kegagalan atau pemblokiran login.
    """
    template_name = 'registration/login.html'

    def _get_max_attempts(self) -> int:
        return getattr(settings, 'LOGIN_MAX_FAILED_ATTEMPTS', 5)

    def _get_lockout_duration_delta(self) -> timedelta:
        seconds = getattr(settings, 'LOGIN_LOCKOUT_DURATION', 900)
        return timedelta(seconds=seconds)

    def _check_lockout(self, request, username: str = ''):
        """Memeriksa apakah IP atau Username saat ini sedang dalam status terkunci di database."""
        now = timezone.now()
        ip = get_client_ip(request)
        clean_user = (username or '').strip().lower()

        identifiers = [f'ip:{ip}']
        if clean_user:
            identifiers.append(f'user:{clean_user}')

        locked_record = LoginSecurityRecord.objects.filter(
            identifier__in=identifiers,
            locked_until__gt=now,
        ).order_by('-locked_until').first()

        if locked_record:
            remaining_mins = max(1, math.ceil((locked_record.locked_until - now).total_seconds() / 60))
            if locked_record.identifier.startswith('ip:'):
                return True, f"Terlalu banyak percobaan login gagal dari IP Anda ({ip}). Akses dikunci sementara selama {remaining_mins} menit."
            else:
                return True, f"Akun '{clean_user}' dikunci sementara selama {remaining_mins} menit karena terlalu banyak percobaan login yang gagal."

        return False, None

    def post(self, request, *args, **kwargs):
        username = request.POST.get('username', '').strip()
        is_locked, lock_message = self._check_lockout(request, username)
        if is_locked:
            ip = get_client_ip(request)
            logger.warning(
                "Akses login DITOLAK (Locked Out): User '%s' dari IP %s. Alasan: %s",
                username,
                ip,
                lock_message,
            )
            form = self.get_form()
            form.add_error(None, lock_message)
            return self.render_to_response(self.get_context_data(form=form))

        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        username = self.request.POST.get('username', '').strip()
        now = timezone.now()
        duration_delta = self._get_lockout_duration_delta()
        max_attempts = self._get_max_attempts()
        ip = get_client_ip(self.request)
        clean_user = (username or '').strip().lower()

        # Update or create security records for IP and user
        targets = [f'ip:{ip}']
        if clean_user:
            targets.append(f'user:{clean_user}')

        ip_record = None
        user_record = None

        for ident in targets:
            rec, _ = LoginSecurityRecord.objects.get_or_create(identifier=ident)
            # Reset jika percobaan gagal sebelumnya sudah lewat dari window durasi lockout
            if rec.last_attempt_at and (now - rec.last_attempt_at) > duration_delta:
                rec.failed_count = 0
                rec.locked_until = None

            rec.failed_count += 1
            if rec.failed_count >= max_attempts:
                rec.locked_until = now + duration_delta

            rec.save()

            if ident.startswith('ip:'):
                ip_record = rec
            else:
                user_record = rec

        logger.warning(
            "Login gagal untuk user '%s' dari IP %s (IP attempts: %d/%d, User attempts: %d/%d)",
            username,
            ip,
            ip_record.failed_count if ip_record else 0,
            max_attempts,
            user_record.failed_count if user_record else 0,
            max_attempts,
        )

        # Tambahkan pesan lockout atau peringatan jika mendekati batas
        remaining_mins = math.ceil(duration_delta.total_seconds() / 60)
        if ip_record and ip_record.failed_count >= max_attempts:
            form.add_error(None, f"Terlalu banyak percobaan login gagal dari IP Anda ({ip}). Akses dikunci sementara selama {remaining_mins} menit.")
        elif user_record and user_record.failed_count >= max_attempts:
            form.add_error(None, f"Akun '{clean_user}' dikunci sementara selama {remaining_mins} menit karena terlalu banyak percobaan login yang gagal.")
        else:
            max_failed = max(ip_record.failed_count if ip_record else 0, user_record.failed_count if user_record else 0)
            remaining_tries = max_attempts - max_failed
            if remaining_tries <= 2:
                form.add_error(None, f"Peringatan: Tersisa {remaining_tries} percobaan lagi sebelum akun/IP dikunci sementara.")

        return super().form_invalid(form)

    def form_valid(self, form):
        username = form.cleaned_data.get('username') or self.request.POST.get('username', '').strip()
        ip = get_client_ip(self.request)
        clean_user = (username or '').strip().lower()

        # Reset record kegagalan saat login berhasil
        targets = [f'ip:{ip}']
        if clean_user:
            targets.append(f'user:{clean_user}')
        LoginSecurityRecord.objects.filter(identifier__in=targets).delete()

        logger.info("Login BERHASIL untuk user '%s' dari IP %s.", username, ip)
        return super().form_valid(form)
