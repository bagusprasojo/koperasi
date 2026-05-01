from django.urls import path

from . import views

urlpatterns = [
    path('products/', views.product_list, name='product_list'),
    path('purchases/', views.purchase_page, name='purchase_page'),
]
