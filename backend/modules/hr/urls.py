from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("hr/positions", views.PositionViewSet, basename="hr-position")
router.register("hr/contracts", views.ContractViewSet, basename="hr-contract")
router.register("hr/profiles", views.EmployeeProfileViewSet, basename="hr-profile")
router.register("hr/documents", views.StaffDocumentViewSet, basename="hr-document")
router.register("hr/fiscal-years", views.FiscalYearViewSet, basename="hr-fiscal-year")
router.register("hr/leave-types", views.LeaveTypeViewSet, basename="hr-leave-type")
router.register("hr/leave-balances", views.LeaveBalanceViewSet, basename="hr-leave-balance")
router.register("hr/leave-requests", views.LeaveRequestViewSet, basename="hr-leave-request")

urlpatterns = router.urls
