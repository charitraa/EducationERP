from rest_framework.routers import DefaultRouter

from .views import ParentViewSet

router = DefaultRouter()
router.register("parents", ParentViewSet, basename="parent")

urlpatterns = router.urls
