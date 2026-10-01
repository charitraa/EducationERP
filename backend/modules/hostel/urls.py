from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("hostel/buildings", views.BuildingViewSet, basename="hostel-building")
router.register("hostel/floors", views.FloorViewSet, basename="hostel-floor")
router.register("hostel/room-types", views.RoomTypeViewSet, basename="hostel-room-type")
router.register("hostel/rooms", views.HostelRoomViewSet, basename="hostel-room")
router.register("hostel/beds", views.BedViewSet, basename="hostel-bed")
router.register("hostel/allocations", views.AllocationViewSet, basename="hostel-allocation")
router.register("hostel/complaints", views.ComplaintViewSet, basename="hostel-complaint")

urlpatterns = router.urls
