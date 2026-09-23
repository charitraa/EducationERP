from django.contrib.auth import user_logged_in, user_logged_out
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from core.accounts.serializers import (
    ChangePasswordSerializer,
    CurrentUserSerializer,
    CurrentUserUpdateSerializer,
)
from core.accounts.services import change_password

from . import two_factor
from .serializers import (
    LoginSerializer,
    LogoutSerializer,
    RecoveryCodesSerializer,
    TwoFactorCodeSerializer,
    TwoFactorDisableSerializer,
    TwoFactorSetupResponseSerializer,
    TwoFactorSetupSerializer,
    TwoFactorStatusSerializer,
)


@extend_schema(tags=["auth"], summary="Log in and obtain a token pair")
class LoginView(TokenObtainPairView):
    serializer_class = LoginSerializer
    permission_classes = [AllowAny]
    throttle_scope = "login"

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Drives last-login bookkeeping (Django's own receiver) and the
        # audit trail (core.audit.signals).
        user = serializer.user
        user_logged_in.send(sender=user.__class__, request=request, user=user)

        return Response(serializer.validated_data, status=status.HTTP_200_OK)


@extend_schema(tags=["auth"], summary="Exchange a refresh token for a new access token")
class RefreshView(TokenRefreshView):
    permission_classes = [AllowAny]
    throttle_scope = "login"


@extend_schema(
    tags=["auth"],
    summary="Log out",
    request=LogoutSerializer,
    responses={204: None},
)
class LogoutView(GenericAPIView):
    """Blacklists the supplied refresh token so it cannot be reused."""

    serializer_class = LogoutSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data["refresh"]).blacklist()
        except TokenError:
            return Response(
                {"error": {"code": "invalid_token", "message": "Token is invalid or already blacklisted.", "details": None}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_logged_out.send(
            sender=request.user.__class__, request=request, user=request.user
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class CurrentUserView(GenericAPIView):
    """The signed-in user, their organization, roles and permission codes."""

    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method in ("PATCH", "PUT"):
            return CurrentUserUpdateSerializer
        return CurrentUserSerializer

    def get_object(self):
        return self.request.user

    @extend_schema(tags=["auth"], summary="Current user", responses={200: CurrentUserSerializer})
    def get(self, request):
        return Response(CurrentUserSerializer(request.user, context=self.get_serializer_context()).data)

    @extend_schema(
        tags=["auth"],
        summary="Update own contact details",
        request=CurrentUserUpdateSerializer,
        responses={200: CurrentUserSerializer},
    )
    def patch(self, request):
        from core.audit.services import log_update, snapshot

        before = snapshot(request.user)
        serializer = CurrentUserUpdateSerializer(
            request.user, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_update(request, request.user, before=before, module="accounts")
        return Response(
            CurrentUserSerializer(request.user, context=self.get_serializer_context()).data
        )


@extend_schema(
    tags=["auth"],
    summary="Change own password",
    request=ChangePasswordSerializer,
    responses={204: None},
)
class ChangePasswordView(GenericAPIView):
    serializer_class = ChangePasswordSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change_password(
            user=request.user,
            new_password=serializer.validated_data["new_password"],
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Two-factor login (authenticator app)
# ---------------------------------------------------------------------------
class TwoFactorStatusView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["auth"], summary="Is two-factor login on for me?", responses={200: TwoFactorStatusSerializer})
    def get(self, request):
        return Response(two_factor.status(request.user))


class TwoFactorSetupView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = TwoFactorSetupSerializer

    @extend_schema(
        tags=["auth"],
        summary="Start two-factor setup: returns the secret to add to an authenticator app",
        responses={200: TwoFactorSetupResponseSerializer},
    )
    def post(self, request):
        self.get_serializer(data=request.data).is_valid(raise_exception=True)
        secret, uri = two_factor.begin_setup(request.user)
        return Response({"secret": secret, "otpauth_uri": uri})


class TwoFactorConfirmView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = TwoFactorCodeSerializer

    @extend_schema(
        tags=["auth"],
        summary="Finish setup with a code from the app; returns recovery codes (shown once)",
        responses={200: RecoveryCodesSerializer},
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        codes = two_factor.confirm_setup(request.user, serializer.validated_data["code"])
        return Response({"recovery_codes": codes})


class TwoFactorRecoveryCodesView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = TwoFactorCodeSerializer

    @extend_schema(
        tags=["auth"],
        summary="Replace all recovery codes (needs a current app code)",
        responses={200: RecoveryCodesSerializer},
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not two_factor.verify(request.user, serializer.validated_data["code"]):
            return Response(
                {"error": {"code": "invalid_otp", "message": "That two-factor code is not valid.", "details": None}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"recovery_codes": two_factor.regenerate_recovery_codes(request.user)})


class TwoFactorDisableView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = TwoFactorDisableSerializer

    @extend_schema(tags=["auth"], summary="Turn two-factor login off (needs password and a code)", responses={204: None})
    def post(self, request):
        self.get_serializer(data=request.data).is_valid(raise_exception=True)
        two_factor.disable(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
