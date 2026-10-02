from django.conf import settings
from django.contrib.auth import user_logged_in
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, serializers as drf_serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.accounts.serializers import CurrentUserSerializer
from core.audit.middleware import get_client_ip
from core.authentication.serializers import LoginSerializer
from core.common.exceptions import PermissionDeniedError
from core.common.permissions import IsPlatformAdmin
from core.organizations.models import Organization, code_validator
from integrations.captcha import base as captcha

from . import services
from .models import SignupRequest
from .serializers import (
    AcceptedSerializer,
    CheckCodeSerializer,
    CodeAvailabilitySerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RejectSignupSerializer,
    ResendSerializer,
    SignupConfigSerializer,
    SignupRequestSerializer,
    StartSignupSerializer,
    VerifiedSerializer,
    VerifySerializer,
)

TAG = "signup"
CHECK_EMAIL = "If the details are right, an email is on its way. Open the link in it to continue."


class PublicView(APIView):
    """No sign-in, no credentials read: a stray Authorization header can't
    turn a public form into a 401."""

    permission_classes = [AllowAny]
    authentication_classes = []


class SignupView(PublicView):
    throttle_scope = "signup"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not settings.SIGNUP_ENABLED:
            raise PermissionDeniedError("Signup is closed on this server.", code="signup_disabled")


@extend_schema(tags=[TAG], summary="What the signup form needs: open or not, CAPTCHA widget, organization types",
               responses={200: SignupConfigSerializer})
class SignupConfigView(PublicView):
    throttle_scope = "signup_check"

    def get(self, request):
        config = captcha.public_config()
        return Response({
            "enabled": settings.SIGNUP_ENABLED,
            "requires_approval": settings.SIGNUP_REQUIRE_APPROVAL,
            "captcha_provider": config["provider"],
            "captcha_site_key": config["site_key"],
            "organization_types": [{"value": v, "label": str(label)} for v, label in Organization.Type.choices],
        })


@extend_schema(tags=[TAG], summary="Sign up a new organization (emails a link; nothing is created until it's opened)",
               request=StartSignupSerializer, responses={202: AcceptedSerializer})
class StartSignupView(SignupView):
    def post(self, request):
        serializer = StartSignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        captcha.require(request, data.get("captcha_token", ""))
        services.start_signup(
            organization_name=data["organization_name"], organization_code=data["organization_code"],
            organization_type=data["organization_type"], timezone_name=data["timezone"],
            admin_email=data["email"], password=data["password"], admin_first_name=data["first_name"],
            admin_last_name=data["last_name"], admin_phone=data["phone"], ip_address=get_client_ip(request),
        )
        return Response({"detail": CHECK_EMAIL}, status=status.HTTP_202_ACCEPTED)


@extend_schema(tags=[TAG], summary="Send the verification link again (the old one stops working)",
               request=ResendSerializer, responses={202: AcceptedSerializer})
class ResendView(SignupView):
    def post(self, request):
        serializer = ResendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        captcha.require(request, serializer.validated_data.get("captcha_token", ""))
        services.resend_link(serializer.validated_data["email"])
        return Response({"detail": CHECK_EMAIL}, status=status.HTTP_202_ACCEPTED)


@extend_schema(tags=[TAG], summary="Open the emailed link: creates the organization and signs you in "
                                   "(or queues it for approval)",
               request=VerifySerializer, responses={201: VerifiedSerializer, 202: VerifiedSerializer})
class VerifyView(SignupView):
    throttle_scope = "signup_check"

    def post(self, request):
        serializer = VerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        signup, admin = services.verify_signup(serializer.validated_data["token"])
        if admin is None:
            return Response({"status": signup.status, "organization": None}, status=status.HTTP_202_ACCEPTED)

        # Signed in straight away, exactly as a login would.
        user_logged_in.send(sender=admin.__class__, request=request, user=admin)
        refresh = LoginSerializer.get_token(admin)
        organization = signup.organization
        return Response({
            "status": signup.status,
            "organization": {"id": organization.pk, "name": organization.name, "code": organization.code},
            "refresh": str(refresh), "access": str(refresh.access_token),
            "user": CurrentUserSerializer(admin, context={"request": request}).data,
        }, status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG], summary="Is an organization code free?",
               parameters=[OpenApiParameter("code", str, required=True)],
               responses={200: CodeAvailabilitySerializer})
class CheckCodeView(SignupView):
    throttle_scope = "signup_check"

    def get(self, request):
        serializer = CheckCodeSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data["code"].strip().lower()
        try:
            code_validator(code)
            reason = None if len(code) >= 2 else "invalid"
        except DjangoValidationError:
            reason = "invalid"
        reason = reason or services.code_unavailable_reason(code)
        return Response({"code": code, "available": reason is None, "reason": reason})


@extend_schema_view(
    list=extend_schema(tags=[TAG], summary="Signup requests (platform admins)"),
    retrieve=extend_schema(tags=[TAG], summary="A signup request (platform admins)"),
)
class SignupRequestViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Every signup, for platform admins: approve or reject the verified
    ones when ``SIGNUP_REQUIRE_APPROVAL`` is on."""

    permission_classes = [IsPlatformAdmin]
    queryset = SignupRequest.objects.select_related("organization")
    serializer_class = SignupRequestSerializer
    filterset_fields = ["status", "organization_type"]
    search_fields = ["organization_name", "organization_code", "admin_email"]
    ordering_fields = ["created_at", "verified_at"]

    @extend_schema(tags=[TAG], summary="Approve: creates the organization and emails its admin",
                   request=None, responses={200: SignupRequestSerializer})
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return Response(SignupRequestSerializer(services.approve(self.get_object(), by=request.user)).data)

    @extend_schema(tags=[TAG], summary="Reject (the person is emailed the reason)",
                   request=RejectSignupSerializer, responses={200: SignupRequestSerializer})
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        serializer = RejectSignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        signup = services.reject(self.get_object(), by=request.user, reason=serializer.validated_data["reason"])
        return Response(SignupRequestSerializer(signup).data)


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------
@extend_schema(tags=["auth"], summary="Forgot password: emails a reset link (same answer whether or not "
                                      "the account exists)",
               request=PasswordResetRequestSerializer, responses={202: AcceptedSerializer})
class PasswordResetRequestView(PublicView):
    throttle_scope = "password_reset"

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.request_password_reset(serializer.validated_data["email"])
        return Response({"detail": "If an account uses that email, a reset link is on its way."},
                        status=status.HTTP_202_ACCEPTED)


@extend_schema(tags=["auth"], summary="Choose a new password from the emailed link (signs out everywhere)",
               request=PasswordResetConfirmSerializer, responses={204: None})
class PasswordResetConfirmView(PublicView):
    throttle_scope = "password_reset"

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = services.user_for_reset(data["uid"], data["token"])
        try:
            validate_password(data["new_password"], user)
        except DjangoValidationError as exc:
            raise drf_serializers.ValidationError({"new_password": list(exc.messages)}) from exc
        services.reset_password(user, data["new_password"])
        return Response(status=status.HTTP_204_NO_CONTENT)
