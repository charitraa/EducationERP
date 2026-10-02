from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("signup-requests", views.SignupRequestViewSet, basename="signup-request")

urlpatterns = [
    path("signup/", views.StartSignupView.as_view(), name="signup"),
    path("signup/config/", views.SignupConfigView.as_view(), name="signup-config"),
    path("signup/resend/", views.ResendView.as_view(), name="signup-resend"),
    path("signup/verify/", views.VerifyView.as_view(), name="signup-verify"),
    path("signup/check-code/", views.CheckCodeView.as_view(), name="signup-check-code"),
    path("auth/password-reset/", views.PasswordResetRequestView.as_view(), name="password-reset"),
    path("auth/password-reset/confirm/", views.PasswordResetConfirmView.as_view(), name="password-reset-confirm"),
    *router.urls,
]
