from django.urls import path

from . import views

urlpatterns = [
    path('products/', views.product_list, name='product_list'),
    path('products/create/', views.product_create, name='product_create'),
    path('products/<uuid:uuid>/', views.product_detail, name='product_detail'),
    path('products/<uuid:uuid>/edit/', views.product_edit, name='product_edit'),
    path('products/<uuid:uuid>/delete/', views.product_delete, name='product_delete'),
    path('purchases/', views.purchase_page, name='purchase_page'),
    path('categories/', views.category_list, name='category_list'),
    path('categories/create/', views.category_create, name='category_create'),
    path('categories/<uuid:uuid>/', views.category_detail, name='category_detail'),
    path('categories/<uuid:uuid>/edit/', views.category_edit, name='category_edit'),
    path('categories/<uuid:uuid>/delete/', views.category_delete, name='category_delete'),
]
