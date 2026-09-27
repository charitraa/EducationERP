from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("fee-categories", views.FeeCategoryViewSet, basename="fee-category")
router.register("fee-structures", views.FeeStructureViewSet, basename="fee-structure")
router.register("scholarships", views.ScholarshipViewSet, basename="scholarship")
router.register("student-scholarships", views.StudentScholarshipViewSet, basename="student-scholarship")
router.register("invoices/reports", views.FinanceReportViewSet, basename="finance-report")
router.register("invoices", views.InvoiceViewSet, basename="invoice")
router.register("payments", views.PaymentViewSet, basename="payment")
router.register("receipts", views.ReceiptViewSet, basename="receipt")
router.register("refunds", views.RefundViewSet, basename="refund")

urlpatterns = [
    path("invoices/assess-late-fees/", views.AssessLateFeesView.as_view(), name="assess-late-fees"),
    *router.urls,
]
