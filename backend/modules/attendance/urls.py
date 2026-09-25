from django.urls import path
from rest_framework.routers import DefaultRouter

from integrations.biometric.api import DevicePunchView

from . import views

router = DefaultRouter()
router.register("attendance/sessions", views.AttendanceSessionViewSet, basename="attendance-session")
router.register("attendance/records", views.AttendanceRecordViewSet, basename="attendance-record")
router.register("attendance/reports", views.AttendanceReportViewSet, basename="attendance-report")
router.register("attendance/staff-days", views.StaffAttendanceDayViewSet, basename="staff-attendance-day")
router.register("attendance/punches", views.PunchViewSet, basename="attendance-punch")
router.register("attendance/work-schedules", views.WorkScheduleViewSet, basename="work-schedule")
router.register("attendance/staff-schedules", views.StaffWorkScheduleViewSet, basename="staff-work-schedule")
router.register("attendance/devices", views.AttendanceDeviceViewSet, basename="attendance-device")
router.register("attendance/biometric-ids", views.BiometricIdentityViewSet, basename="biometric-identity")

urlpatterns = [
    path("attendance/device-punches/", DevicePunchView.as_view(), name="attendance-device-punches"),
    *router.urls,
]
