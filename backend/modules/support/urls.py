from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("support/tickets", views.SupportTicketViewSet, basename="support-ticket")

urlpatterns = router.urls
