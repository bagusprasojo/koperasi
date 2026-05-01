from django.urls import path

from . import views

urlpatterns = [
    path('products/', views.product_list, name='product_list'),
    path('purchases/', views.purchase_page, name='purchase_page'),
    path('categories/', views.category_list, name='category_list'),
    path('categories/<uuid:uuid>/', views.category_detail, name='category_detail'),
    path('categories/<uuid:uuid>/edit/', views.category_edit, name='category_edit'),
    path('categories/<uuid:uuid>/delete/', views.category_delete, name='category_delete'),
]
