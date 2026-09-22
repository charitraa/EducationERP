from rest_framework.routers import DefaultRouter

from .views import CampusViewSet, OrganizationViewSet

router = DefaultRouter()
router.register("organizations", OrganizationViewSet, basename="organization")
router.register("campuses", CampusViewSet, basename="campus")

urlpatterns = router.urls
