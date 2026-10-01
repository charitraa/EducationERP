from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("payroll/settings", views.PayrollSettingsViewSet, basename="payroll-settings")
router.register("payroll/components", views.PayComponentViewSet, basename="payroll-component")
router.register("payroll/structures", views.SalaryStructureViewSet, basename="payroll-structure")
router.register("payroll/staff-salaries", views.StaffSalaryViewSet, basename="payroll-staff-salary")
router.register("payroll/tax-schemes", views.TaxSchemeViewSet, basename="payroll-tax-scheme")
router.register("payroll/runs", views.PayrollRunViewSet, basename="payroll-run")
router.register("payroll/payslips", views.PayslipViewSet, basename="payroll-payslip")
router.register("payroll/adjustments", views.PayrollAdjustmentViewSet, basename="payroll-adjustment")

urlpatterns = router.urls
