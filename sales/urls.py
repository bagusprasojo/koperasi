from django.urls import path

from . import views

urlpatterns = [
    path('pos/', views.pos_page, name='pos_page'),
    path('reports/daily-summary/', views.sales_daily_summary, name='sales_daily_summary'),
    path('reports/daily-summary/<str:tx_date>/', views.sales_daily_summary_detail, name='sales_daily_summary_detail'),
    path('reports/sale/<str:sale_number>/', views.sales_transaction_detail, name='sales_transaction_detail'),
    path('reports/daily-summary/export/csv/', views.sales_daily_summary_export_csv, name='sales_daily_summary_export_csv'),
    path('reports/product-sales/', views.sales_product_report, name='sales_product_report'),
    path('reports/product-sales/export/csv/', views.sales_product_report_export_csv, name='sales_product_report_export_csv'),
    path('pos/api/members', views.pos_member_search_api, name='pos_member_search_api'),
    path('pos/api/price-preview', views.pos_price_preview_api, name='pos_price_preview_api'),
    path('pos/api/checkout', views.pos_checkout_api, name='pos_checkout_api'),
    path('pos/api/receipt/<str:sale_number>', views.pos_receipt_detail_api, name='pos_receipt_detail_api'),
    path('pos/api/reprint/<str:sale_number>', views.pos_reprint_api, name='pos_reprint_api'),
    path('pos/api/print/dispatch-pending', views.pos_dispatch_pending_print_jobs_api, name='pos_dispatch_pending_print_jobs_api'),
]
