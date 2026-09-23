from django.db import models


class Role(models.TextChoices):
    ADMIN_TOKO = 'admin_toko', 'Admin Toko'
    KASIR = 'kasir', 'Kasir'
    PEMBELIAN = 'pembelian', 'Bagian Pembelian'
    MEMBER = 'member', 'Anggota'


# Role combinations for convenience
STAFF_ROLES = (Role.ADMIN_TOKO, Role.KASIR, Role.PEMBELIAN)
MANAGEMENT_ROLES = (Role.ADMIN_TOKO, Role.PEMBELIAN)
CASHIER_ROLES = (Role.ADMIN_TOKO, Role.KASIR)
ALL_ROLES = tuple(Role.values)
