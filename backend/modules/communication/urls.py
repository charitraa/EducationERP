from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("communication/threads", views.MessageThreadViewSet, basename="message-thread")
router.register("communication/appointment-slots", views.AppointmentSlotViewSet, basename="appointment-slot")
router.register("communication/appointments", views.AppointmentViewSet, basename="appointment")

urlpatterns = router.urls
