import logging
import math
import time

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.core.cache import cache

logger = logging.getLogger('security.auth')


def get_client_ip(request) -> str:
    """Mengambil IP address asli klien, mempertimbangkan header reverse proxy."""
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or '127.0.0.1'


class RateLimitedLoginView(auth_views.LoginView):
    """
    Subclass LoginView dengan proteksi anti brute-force dan rate-limiting berbasis Django Cache:
    - Melacak percobaan gagal berdasarkan kombinasi Alamat IP dan Username.
    - Mengunci login selama LOGIN_LOCKOUT_DURATION detik jika gagal mencapai LOGIN_MAX_FAILED_ATTEMPTS kali.
    - Mencatat log audit keamanan saat terjadi kegagalan atau pemblokiran login.
    """
    template_name = 'registration/login.html'

    def _get_max_attempts(self) -> int:
        return getattr(settings, 'LOGIN_MAX_FAILED_ATTEMPTS', 5)

    def _get_lockout_duration(self) -> int:
        return getattr(settings, 'LOGIN_LOCKOUT_DURATION', 900)

    def _cache_keys(self, request, username: str = ''):
        ip = get_client_ip(request)
        clean_user = (username or '').strip().lower()
        ip_attempts_key = f'auth_attempts_ip:{ip}'
        user_attempts_key = f'auth_attempts_user:{clean_user}' if clean_user else None
        ip_lock_key = f'auth_lock_ip:{ip}'
        user_lock_key = f'auth_lock_user:{clean_user}' if clean_user else None
        return ip, clean_user, ip_attempts_key, user_attempts_key, ip_lock_key, user_lock_key

    def _check_lockout(self, request, username: str = ''):
        """Memeriksa apakah IP atau Username saat ini sedang dalam status terkunci."""
        now = time.time()
        ip, clean_user, _, _, ip_lock_key, user_lock_key = self._cache_keys(request, username)

        ip_lock_until = cache.get(ip_lock_key)
        if ip_lock_until and ip_lock_until > now:
            remaining = max(1, math.ceil((ip_lock_until - now) / 60))
            return True, f"Terlalu banyak percobaan login gagal dari IP Anda ({ip}). Akses dikunci sementara selama {remaining} menit."

        if user_lock_key:
            user_lock_until = cache.get(user_lock_key)
            if user_lock_until and user_lock_until > now:
                remaining = max(1, math.ceil((user_lock_until - now) / 60))
                return True, f"Akun '{clean_user}' dikunci sementara selama {remaining} menit karena terlalu banyak percobaan login yang gagal."

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
        now = time.time()
        duration = self._get_lockout_duration()
        max_attempts = self._get_max_attempts()
        ip, clean_user, ip_att_key, user_att_key, ip_lock_key, user_lock_key = self._cache_keys(self.request, username)

        # Increment IP attempts
        ip_attempts = cache.get(ip_att_key, 0) + 1
        cache.set(ip_att_key, ip_attempts, timeout=duration)

        # Increment User attempts
        user_attempts = 0
        if user_att_key:
            user_attempts = cache.get(user_att_key, 0) + 1
            cache.set(user_att_key, user_attempts, timeout=duration)

        logger.warning(
            "Login gagal untuk user '%s' dari IP %s (IP attempts: %d/%d, User attempts: %d/%d)",
            username,
            ip,
            ip_attempts,
            max_attempts,
            user_attempts,
            max_attempts,
        )

        # Periksa apakah mencapai batas penguncian
        if ip_attempts >= max_attempts:
            cache.set(ip_lock_key, now + duration, timeout=duration)
            logger.error("IP %s DIKUNCI sementara selama %d detik karena gagal login %d kali.", ip, duration, max_attempts)
            remaining_mins = math.ceil(duration / 60)
            form.add_error(None, f"Terlalu banyak percobaan login gagal dari IP Anda ({ip}). Akses dikunci sementara selama {remaining_mins} menit.")
        elif user_attempts >= max_attempts and clean_user:
            cache.set(user_lock_key, now + duration, timeout=duration)
            logger.error("User '%s' DIKUNCI sementara selama %d detik karena gagal login %d kali.", clean_user, duration, max_attempts)
            remaining_mins = math.ceil(duration / 60)
            form.add_error(None, f"Akun '{clean_user}' dikunci sementara selama {remaining_mins} menit karena terlalu banyak percobaan login yang gagal.")
        else:
            remaining_tries = max_attempts - max(ip_attempts, user_attempts)
            if remaining_tries <= 2:
                form.add_error(None, f"Peringatan: Tersisa {remaining_tries} percobaan lagi sebelum akun/IP dikunci sementara.")

        return super().form_invalid(form)

    def form_valid(self, form):
        username = form.cleaned_data.get('username') or self.request.POST.get('username', '').strip()
        ip, clean_user, ip_att_key, user_att_key, ip_lock_key, user_lock_key = self._cache_keys(self.request, username)

        # Reset counters on success
        cache.delete(ip_att_key)
        cache.delete(ip_lock_key)
        if user_att_key:
            cache.delete(user_att_key)
        if user_lock_key:
            cache.delete(user_lock_key)

        logger.info("Login BERHASIL untuk user '%s' dari IP %s.", username, ip)
        return super().form_valid(form)
