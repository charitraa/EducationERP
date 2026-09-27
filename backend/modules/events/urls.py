from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("event-categories", views.EventCategoryViewSet, basename="event-category")
router.register("events", views.EventViewSet, basename="event")
router.register("event-registrations", views.EventRegistrationViewSet, basename="event-registration")
router.register("point-rules", views.PointRuleViewSet, basename="point-rule")
router.register("point-entries", views.PointEntryViewSet, basename="point-entry")
router.register("student-points", views.StudentPointsViewSet, basename="student-points")
router.register("awards", views.AwardViewSet, basename="award")
router.register("award-rules", views.AwardRuleViewSet, basename="award-rule")
router.register("student-awards", views.StudentAwardViewSet, basename="student-award")

urlpatterns = router.urls
