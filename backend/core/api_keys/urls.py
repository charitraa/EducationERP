from rest_framework.routers import DefaultRouter

from .views import ApiKeyViewSet

router = DefaultRouter()
router.register("api-keys", ApiKeyViewSet, basename="api-key")

urlpatterns = router.urls
