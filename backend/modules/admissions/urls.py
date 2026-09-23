from rest_framework.routers import DefaultRouter

from .views import AdmissionViewSet

router = DefaultRouter()
router.register("admissions", AdmissionViewSet, basename="admission")

urlpatterns = router.urls
