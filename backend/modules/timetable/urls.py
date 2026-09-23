from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("bell-schedules", views.BellScheduleViewSet, basename="bell-schedule")
router.register("periods", views.PeriodViewSet, basename="period")
router.register("timetable", views.TimetableEntryViewSet, basename="timetable-entry")
router.register("lesson-changes", views.LessonChangeViewSet, basename="lesson-change")

urlpatterns = router.urls
