from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("transport/vehicles", views.VehicleViewSet, basename="transport-vehicle")
router.register("transport/vehicle-documents", views.VehicleDocumentViewSet, basename="transport-vehicle-document")
router.register("transport/drivers", views.DriverViewSet, basename="transport-driver")
router.register("transport/routes", views.RouteViewSet, basename="transport-route")
router.register("transport/stops", views.StopViewSet, basename="transport-stop")
router.register("transport/assignments", views.AssignmentViewSet, basename="transport-assignment")
router.register("transport/trips", views.TripViewSet, basename="transport-trip")
router.register("transport/trip-records", views.TripRecordViewSet, basename="transport-trip-record")
router.register("transport/maintenance", views.VehicleMaintenanceViewSet, basename="transport-maintenance")
router.register("transport/fuel-logs", views.FuelLogViewSet, basename="transport-fuel-log")

urlpatterns = router.urls
