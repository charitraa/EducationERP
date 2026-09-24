from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("departments", views.DepartmentViewSet, basename="department")
router.register("programs", views.ProgramViewSet, basename="program")
router.register("subjects", views.SubjectViewSet, basename="subject")
router.register("curriculum", views.CurriculumSubjectViewSet, basename="curriculum")
router.register("academic-years", views.AcademicYearViewSet, basename="academic-year")
router.register("terms", views.TermViewSet, basename="term")
router.register("rooms", views.RoomViewSet, basename="room")
router.register("batches", views.BatchViewSet, basename="batch")
router.register("sections", views.SectionViewSet, basename="section")
router.register("teaching-assignments", views.TeachingAssignmentViewSet, basename="teaching-assignment")
router.register("student-electives", views.StudentElectiveViewSet, basename="student-elective")
router.register("calendar", views.CalendarEventViewSet, basename="calendar-event")

urlpatterns = router.urls
