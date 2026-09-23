from django.urls import path

from .views import (
    ChangePasswordView,
    CurrentUserView,
    LoginView,
    LogoutView,
    RefreshView,
    TwoFactorConfirmView,
    TwoFactorDisableView,
    TwoFactorRecoveryCodesView,
    TwoFactorSetupView,
    TwoFactorStatusView,
)

urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    path("refresh/", RefreshView.as_view(), name="token-refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", CurrentUserView.as_view(), name="current-user"),
    path("change-password/", ChangePasswordView.as_view(), name="change-password"),
    path("2fa/", TwoFactorStatusView.as_view(), name="two-factor-status"),
    path("2fa/setup/", TwoFactorSetupView.as_view(), name="two-factor-setup"),
    path("2fa/confirm/", TwoFactorConfirmView.as_view(), name="two-factor-confirm"),
    path("2fa/recovery-codes/", TwoFactorRecoveryCodesView.as_view(), name="two-factor-recovery-codes"),
    path("2fa/disable/", TwoFactorDisableView.as_view(), name="two-factor-disable"),
]
