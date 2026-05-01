from django.urls import path

from . import views

urlpatterns = [
    path('pos/', views.pos_page, name='pos_page'),
]
