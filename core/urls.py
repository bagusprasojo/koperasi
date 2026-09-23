from django.urls import path

from . import views, views_staff, views_matrix

urlpatterns = [
    path('', views.home, name='home'),
    path('dashboard/', views.dashboard, name='dashboard'),

    # Pendekatan A: Manajemen Staf & Pengguna
    path('staff/', views_staff.staff_list, name='staff_list'),
    path('staff/create/', views_staff.staff_create, name='staff_create'),
    path('staff/<int:user_id>/edit/', views_staff.staff_edit, name='staff_edit'),
    path('staff/<int:user_id>/password/', views_staff.staff_password_reset, name='staff_password_reset'),
    path('staff/<int:user_id>/toggle-active/', views_staff.staff_toggle_active, name='staff_toggle_active'),

    # Pendekatan B: Matriks Hak Akses Dinamis Role
    path('staff/roles/matrix/', views_matrix.role_permission_matrix, name='role_permission_matrix'),
]
