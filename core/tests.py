from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from core.constants import Role, CASHIER_ROLES, STAFF_ROLES
from core.context_processors import role_flags
from core.decorators import role_required
from core.rbac import get_user_roles, has_any_role, has_permission_or_role
from members.models import Member, MemberTopUp
from members.permissions import can_access_topup_proof, can_access_member_profile
from sales.models import Sale
from sales.permissions import can_view_sale_detail

User = get_user_model()


# Sample decorated views for testing
@role_required(Role.ADMIN_TOKO)
def sample_admin_view(request):
    return HttpResponse("admin ok")


@role_required(*CASHIER_ROLES)
def sample_cashier_view(request):
    return HttpResponse("cashier ok")


@role_required(Role.ADMIN_TOKO, perm='void_sale')
def sample_void_view(request):
    return HttpResponse("void ok")


class RBACTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

        # Groups
        self.admin_group, _ = Group.objects.get_or_create(name=Role.ADMIN_TOKO)
        self.kasir_group, _ = Group.objects.get_or_create(name=Role.KASIR)
        self.pembelian_group, _ = Group.objects.get_or_create(name=Role.PEMBELIAN)
        self.member_group, _ = Group.objects.get_or_create(name=Role.MEMBER)

        # Users
        self.superuser = User.objects.create_superuser(username='superadmin', password='password123')
        self.admin_user = User.objects.create_user(username='admintoko', password='password123')
        self.admin_user.groups.add(self.admin_group)

        self.kasir_user = User.objects.create_user(username='kasir1', password='password123')
        self.kasir_user.groups.add(self.kasir_group)

        self.member_user1 = User.objects.create_user(username='member1', password='password123')
        self.member_user1.groups.add(self.member_group)
        self.member1 = Member.objects.create(user=self.member_user1, full_name='Member Satu', phone='0811111111')

        self.member_user2 = User.objects.create_user(username='member2', password='password123')
        self.member_user2.groups.add(self.member_group)
        self.member2 = Member.objects.create(user=self.member_user2, full_name='Member Dua', phone='0822222222')

    def test_superuser_bypass_without_group(self):
        """Superuser tanpa grup apapun harus diizinkan mengakses view yang diproteksi."""
        request = self.factory.get('/dummy-admin/')
        request.user = self.superuser
        response = sample_admin_view(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "admin ok")

        response_cashier = sample_cashier_view(request)
        self.assertEqual(response_cashier.status_code, 200)

    def test_role_required_allows_matching_role(self):
        """User dengan role yang sesuai diizinkan mengakses view."""
        request = self.factory.get('/dummy-cashier/')
        request.user = self.kasir_user
        response = sample_cashier_view(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "cashier ok")

    def test_role_required_denies_mismatched_role(self):
        """User tanpa role yang sesuai harus dilempar 403 PermissionDenied."""
        request = self.factory.get('/dummy-admin/')
        request.user = self.kasir_user  # kasir tidak punya role admin_toko
        with self.assertRaises(PermissionDenied):
            sample_admin_view(request)

    def test_role_caching_on_request(self):
        """Fungsi get_user_roles menyimpan cache di objek request."""
        request = self.factory.get('/')
        request.user = self.kasir_user

        roles_first = get_user_roles(request.user, request=request)
        self.assertIn(Role.KASIR, roles_first)
        self.assertTrue(hasattr(request, '_cached_user_roles'))

        # Query ulang melalui request harus membaca dari cache
        roles_second = get_user_roles(request.user, request=request)
        self.assertIs(roles_first, roles_second)

    def test_has_any_role_helper(self):
        self.assertTrue(has_any_role(self.superuser, Role.ADMIN_TOKO))
        self.assertTrue(has_any_role(self.kasir_user, *CASHIER_ROLES))
        self.assertFalse(has_any_role(self.kasir_user, Role.PEMBELIAN))
        self.assertFalse(has_any_role(AnonymousUser(), Role.MEMBER))

    def test_context_processor_role_flags(self):
        # Anonymous
        req_anon = self.factory.get('/')
        req_anon.user = AnonymousUser()
        flags_anon = role_flags(req_anon)
        self.assertFalse(flags_anon['is_staff_any'])
        self.assertFalse(flags_anon['is_admin_toko_role'])

        # Superuser
        req_super = self.factory.get('/')
        req_super.user = self.superuser
        flags_super = role_flags(req_super)
        self.assertTrue(flags_super['is_admin_toko_role'])
        self.assertTrue(flags_super['is_staff_any'])

        # Kasir
        req_kasir = self.factory.get('/')
        req_kasir.user = self.kasir_user
        flags_kasir = role_flags(req_kasir)
        self.assertTrue(flags_kasir['is_kasir_role'])
        self.assertTrue(flags_kasir['is_staff_any'])
        self.assertFalse(flags_kasir['is_admin_toko_role'])

        # Member
        req_member = self.factory.get('/')
        req_member.user = self.member_user1
        flags_member = role_flags(req_member)
        self.assertTrue(flags_member['is_member_role'])
        self.assertFalse(flags_member['is_staff_any'])

    def test_can_access_topup_proof(self):
        topup1 = MemberTopUp.objects.create(
            member=self.member1,
            amount=Decimal('50000.00'),
            requested_by=self.member_user1,
        )

        # Pemilik berhak
        self.assertTrue(can_access_topup_proof(self.member_user1, topup1))
        # Admin toko & Superuser berhak
        self.assertTrue(can_access_topup_proof(self.admin_user, topup1))
        self.assertTrue(can_access_topup_proof(self.superuser, topup1))
        # Member lain tidak berhak
        self.assertFalse(can_access_topup_proof(self.member_user2, topup1))
        # Kasir biasa tidak berhak jika bukan admin
        self.assertFalse(can_access_topup_proof(self.kasir_user, topup1))

    def test_can_view_sale_detail(self):
        sale1 = Sale.objects.create(
            sale_number='SL-TEST-001',
            member=self.member1,
            subtotal=Decimal('10000.00'),
            total=Decimal('10000.00'),
            created_by=self.kasir_user,
        )

        # Kasir dan Admin Toko berhak
        self.assertTrue(can_view_sale_detail(self.kasir_user, sale1))
        self.assertTrue(can_view_sale_detail(self.admin_user, sale1))
        self.assertTrue(can_view_sale_detail(self.superuser, sale1))
        # Member pemilik berhak
        self.assertTrue(can_view_sale_detail(self.member_user1, sale1))
        # Member lain tidak berhak
        self.assertFalse(can_view_sale_detail(self.member_user2, sale1))

    # --- Pengujian Pendekatan A: Manajemen Staf ---
    def test_staff_management_access_control(self):
        from core.views_staff import staff_list

        # Kasir ditolak saat mencoba membuka daftar staf
        req = self.factory.get('/staff/')
        req.user = self.kasir_user
        with self.assertRaises(PermissionDenied):
            staff_list(req)

        # Admin toko diizinkan
        req_admin = self.factory.get('/staff/')
        req_admin.user = self.admin_user
        setattr(req_admin, 'session', {})
        setattr(req_admin, '_messages', FallbackStorage(req_admin))
        resp = staff_list(req_admin)
        self.assertEqual(resp.status_code, 200)

    def test_staff_create_success(self):
        from core.views_staff import staff_create

        req = self.factory.post('/staff/create/', {
            'username': 'kasir_baru',
            'first_name': 'Budi',
            'last_name': 'Kasir',
            'email': 'budi@test.id',
            'password': 'StrongPassword123!',
            'password_confirm': 'StrongPassword123!',
            'roles': [Role.KASIR.value],
            'is_active': 'on',
        })
        req.user = self.admin_user
        setattr(req, 'session', {})
        setattr(req, '_messages', FallbackStorage(req))

        resp = staff_create(req)
        self.assertEqual(resp.status_code, 302)

        new_user = User.objects.filter(username='kasir_baru').first()
        self.assertIsNotNone(new_user)
        self.assertTrue(new_user.groups.filter(name=Role.KASIR).exists())
        self.assertTrue(new_user.is_active)

    def test_staff_edit_self_lockout_protection(self):
        from core.views_staff import staff_edit

        # Admin mencoba menonaktifkan dirinya sendiri
        req = self.factory.post(f'/staff/{self.admin_user.id}/edit/', {
            'first_name': 'Admin',
            'last_name': 'Toko',
            'email': 'admin@test.id',
            'roles': [Role.ADMIN_TOKO.value],
            # is_active tidak dicentang
        })
        req.user = self.admin_user
        setattr(req, 'session', {})
        setattr(req, '_messages', FallbackStorage(req))

        resp = staff_edit(req, self.admin_user.id)
        self.admin_user.refresh_from_db()
        self.assertTrue(self.admin_user.is_active)  # Harus tetap aktif (self-lockout dicegah)

    # --- Pengujian Pendekatan B: Matriks Izin Dinamis ---
    def test_dynamic_permission_matrix_and_enforcement(self):
        from core.views_matrix import role_permission_matrix

        # Awalnya kasir tidak punya izin void_sale
        self.assertFalse(has_permission_or_role(self.kasir_user, 'void_sale'))

        # Kasir mencoba akses view void -> Ditolak (403)
        req_void = self.factory.get('/dummy-void/')
        req_void.user = self.kasir_user
        with self.assertRaises(PermissionDenied):
            sample_void_view(req_void)

        # Admin Toko memperbarui matriks: memberikan izin void_sale ke grup Kasir
        req = self.factory.post('/staff/roles/matrix/', {
            f"perm_{Role.KASIR.value}_access_pos": 'on',
            f"perm_{Role.KASIR.value}_void_sale": 'on',
        })
        req.user = self.admin_user
        setattr(req, 'session', {})
        setattr(req, '_messages', FallbackStorage(req))

        resp = role_permission_matrix(req)
        self.assertEqual(resp.status_code, 302)

        # Reload kasir user dari DB untuk membersihkan cache permissions Django
        self.kasir_user = User.objects.get(id=self.kasir_user.id)

        # Sekarang kasir secara dinamis memiliki izin void_sale!
        self.assertTrue(has_permission_or_role(self.kasir_user, 'void_sale'))

        # Kasir sekarang berhasil mengakses view void!
        req_void2 = self.factory.get('/dummy-void/')
        req_void2.user = self.kasir_user
        resp_void = sample_void_view(req_void2)
        self.assertEqual(resp_void.status_code, 200)
        self.assertEqual(resp_void.content.decode(), "void ok")

    def test_menu_dynamic_permissions_rendering(self):
        from django.contrib.auth.models import Permission
        core_perms = {p.codename: p for p in Permission.objects.filter(content_type__app_label='core')}
        if 'access_pos' in core_perms:
            self.kasir_group.permissions.set([
                core_perms['access_pos'],
                core_perms['view_inventory'],
                core_perms['view_members'],
                core_perms['view_sales_reports'],
            ])
        if 'manage_purchases' in core_perms:
            self.pembelian_group.permissions.set([
                core_perms['manage_purchases'],
                core_perms['manage_products'],
                core_perms['view_inventory'],
                core_perms['view_members'],
                core_perms['view_sales_reports'],
            ])
        if 'manage_staff' in core_perms:
            self.admin_group.permissions.set(list(core_perms.values()))

        pembelian_user = User.objects.create_user(username='pembelian1', password='password123')
        pembelian_user.groups.add(self.pembelian_group)

        # 1. Kasir: Melihat Kasir (POS), tetapi TIDAK melihat Kulakan atau Kelola Staf
        self.client.force_login(self.kasir_user)
        resp_kasir = self.client.get('/dashboard/')
        self.assertEqual(resp_kasir.status_code, 200)
        self.assertContains(resp_kasir, 'Kasir (POS)')
        self.assertNotContains(resp_kasir, 'Kulakan')
        self.assertNotContains(resp_kasir, 'Kelola Staf')

        # 2. Pembelian: Melihat Kulakan, tetapi TIDAK melihat Kasir (POS) atau Kelola Staf
        self.client.force_login(pembelian_user)
        resp_pembelian = self.client.get('/dashboard/')
        self.assertEqual(resp_pembelian.status_code, 200)
        self.assertContains(resp_pembelian, 'Kulakan')
        self.assertNotContains(resp_pembelian, 'Kasir (POS)')
        self.assertNotContains(resp_pembelian, 'Kelola Staf')

        # 3. Admin Toko: Melihat seluruh menu (Kasir POS, Kulakan, Kelola Staf)
        self.client.force_login(self.admin_user)
        resp_admin = self.client.get('/dashboard/')
        self.assertEqual(resp_admin.status_code, 200)
        self.assertContains(resp_admin, 'Kasir (POS)')
        self.assertContains(resp_admin, 'Kulakan')
        self.assertContains(resp_admin, 'Kelola Staf')

        # 4. Member: Melihat portal member, TIDAK melihat modul staf
        self.client.force_login(self.member_user1)
        resp_member = self.client.get('/dashboard/')
        self.assertEqual(resp_member.status_code, 200)
        self.assertContains(resp_member, 'Topup Saya')
        self.assertNotContains(resp_member, 'Kasir (POS)')
        self.assertNotContains(resp_member, 'Kulakan')
        self.assertNotContains(resp_member, 'Kelola Staf')

