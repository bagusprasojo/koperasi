import json
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from core.constants import Role
from members.models import Member, MemberCard
from sales.services import search_members

User = get_user_model()


class PosMemberBarcodeScanTests(TestCase):
    def setUp(self):
        self.kasir_group, _ = Group.objects.get_or_create(name=Role.KASIR)
        self.kasir_user = User.objects.create_user(username='kasir_test', password='password123')
        self.kasir_user.groups.add(self.kasir_group)

        # Create members
        self.m1 = Member.objects.create(
            code='MBR-001',
            full_name='Bagus Prasojo',
            phone='0811112222',
            is_active=True,
        )
        self.card1 = MemberCard.objects.create(
            member=self.m1,
            card_number='345678',
            status=MemberCard.STATUS_ACTIVE,
        )

        self.m2 = Member.objects.create(
            code='MBR-002',
            full_name='Budi Santoso',
            phone='081234567890',  # contains '345678'
            is_active=True,
        )
        self.card2 = MemberCard.objects.create(
            member=self.m2,
            card_number='CRD-888',
            status=MemberCard.STATUS_ACTIVE,
        )

        self.m3 = Member.objects.create(
            code='MBR-999',
            full_name='Siti Aminah',
            phone='0855556666',
            is_active=True,
        )

    def test_search_members_prioritizes_exact_card_number(self):
        results = list(search_members('345678'))
        self.assertGreaterEqual(len(results), 2)
        # Member dengan kartu persis '345678' harus berada di urutan pertama
        self.assertEqual(results[0].id, self.m1.id)
        self.assertEqual(results[0].full_name, 'Bagus Prasojo')
        self.assertEqual(results[0].card.card_number, '345678')

    def test_search_members_matches_member_code(self):
        results = list(search_members('MBR-999'))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, self.m3.id)
        self.assertEqual(results[0].code, 'MBR-999')

    def test_pos_member_search_api_exact_card_flag(self):
        self.client.force_login(self.kasir_user)
        response = self.client.get('/sales/pos/api/members?q=345678')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get('success'))
        rows = data.get('data', [])
        self.assertGreaterEqual(len(rows), 2)

        # First row is exact card match
        first = rows[0]
        self.assertEqual(first['id'], self.m1.id)
        self.assertEqual(first['full_name'], 'Bagus Prasojo')
        self.assertEqual(first['card_number'], '345678')
        self.assertTrue(first['is_exact'])

        # Second row is partial phone match
        second = rows[1]
        self.assertEqual(second['id'], self.m2.id)
        self.assertFalse(second['is_exact'])

    def test_pos_member_search_api_by_member_code(self):
        self.client.force_login(self.kasir_user)
        response = self.client.get('/sales/pos/api/members?q=MBR-001')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get('success'))
        rows = data.get('data', [])
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(rows[0]['code'], 'MBR-001')
        self.assertTrue(rows[0]['is_exact'])
