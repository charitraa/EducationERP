from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("notices", views.NoticeViewSet, basename="notice")

urlpatterns = router.urls
