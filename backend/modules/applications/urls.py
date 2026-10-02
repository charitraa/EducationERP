from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("application-types", views.ApplicationTypeViewSet, basename="application-type")
router.register("applications", views.ApplicationViewSet, basename="application")
router.register("certificates", views.CertificateViewSet, basename="certificate")

PUBLIC = "public/organizations/<slug:code>/"
urlpatterns = router.urls + [
    path(PUBLIC + "application-types/", views.PublicTypesView.as_view(), name="public-application-types"),
    path(PUBLIC + "applications/", views.PublicSubmitView.as_view(), name="public-application-submit"),
    path(PUBLIC + "applications/status/", views.PublicStatusView.as_view(), name="public-application-status"),
    path(PUBLIC + "applications/resubmit/", views.PublicResubmitView.as_view(),
         name="public-application-resubmit"),
    path(PUBLIC + "applications/withdraw/", views.PublicWithdrawView.as_view(), name="public-application-withdraw"),
]
