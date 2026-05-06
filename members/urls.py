from django.urls import path

from . import views

urlpatterns = [
    path('', views.member_list, name='member_list'),
    path('create/', views.member_create, name='member_create'),
    path('<uuid:uuid>/', views.member_detail, name='member_detail'),
    path('<uuid:uuid>/edit/', views.member_edit, name='member_edit'),
    path('<uuid:uuid>/delete/', views.member_delete, name='member_delete'),
    path('cards/', views.card_list, name='card_list'),
    path('cards/create/', views.card_create, name='card_create'),
    path('cards/<uuid:uuid>/', views.card_detail, name='card_detail'),
    path('cards/<uuid:uuid>/edit/', views.card_edit, name='card_edit'),
    path('cards/<uuid:uuid>/delete/', views.card_delete, name='card_delete'),
    path('topup/', views.topup_page, name='member_topup'),
    path('topup/request/', views.member_topup_request, name='member_topup_request'),
    path('me/balance/', views.member_my_balance, name='member_my_balance'),
    path('me/ledger/', views.member_my_ledger, name='member_my_ledger'),
    path('topup/validations/', views.topup_validation_list, name='topup_validation_list'),
    path('topup/<uuid:uuid>/approve/', views.topup_approve_action, name='topup_approve_action'),
    path('topup/<uuid:uuid>/reject/', views.topup_reject_action, name='topup_reject_action'),
    path('topup/<uuid:uuid>/reverse/', views.topup_reverse_action, name='topup_reverse_action'),
    path('topup/bulk/', views.topup_bulk_admin, name='topup_bulk_admin'),
    path('topup/bulk/template.csv', views.topup_bulk_template_csv, name='topup_bulk_template_csv'),
    path('ledger/', views.ledger_list, name='member_ledger'),
]
