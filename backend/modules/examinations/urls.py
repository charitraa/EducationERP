from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("grades/scales", views.GradeScaleViewSet, basename="grade-scale")
router.register("exam-types", views.ExamTypeViewSet, basename="exam-type")
router.register("exams", views.ExamViewSet, basename="exam")
router.register("exam-subjects", views.ExamSubjectViewSet, basename="exam-subject")
router.register("exam-rooms", views.ExamRoomViewSet, basename="exam-room")
router.register("seat-allocations", views.SeatAllocationViewSet, basename="seat-allocation")
router.register("invigilations", views.InvigilationViewSet, basename="invigilation")
router.register("admit-cards", views.AdmitCardViewSet, basename="admit-card")
router.register("mark-sheets", views.MarkSheetViewSet, basename="mark-sheet")
router.register("marks", views.MarkViewSet, basename="mark")
router.register("results", views.ResultViewSet, basename="result")
router.register("term-results", views.ResultPlanViewSet, basename="term-result")
router.register("report-cards", views.ReportCardViewSet, basename="report-card")
router.register("transcripts", views.TranscriptViewSet, basename="transcript")

urlpatterns = router.urls
