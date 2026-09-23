from django.contrib.auth import authenticate
from rest_framework import exceptions, serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from core.accounts.serializers import CurrentUserSerializer
from core.audit.services import log_login_failure

from . import lockout, two_factor

INVALID_CREDENTIALS = "Invalid credentials or inactive account."


class LoginSerializer(TokenObtainPairSerializer):
    """Email + password login returning an access/refresh pair.

    Extra claims are embedded so clients can render without an extra call;
    authoritative permission checks still hit the database on every request.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["email"] = user.email
        token["organization_id"] = user.organization_id
        token["user_type"] = user.user_type
        return token

    otp = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        help_text="Authenticator-app code or a recovery code, for accounts with two-factor login.",
    )

    def validate(self, attrs):
        """Authenticate once and build the token pair.

        Deliberately does not call ``super().validate()``: that would run
        ``authenticate()`` a second time and emit a duplicate
        ``user_login_failed`` signal into the audit trail.
        """
        request = self.context.get("request")
        email = (attrs.get(self.username_field) or "").lower().strip()

        # Checked before the password: a locked account refuses even the right
        # one, so guessing on stays pointless until the window passes.
        if lockout.is_locked(lockout.ACCOUNT, email):
            log_login_failure(email, "locked", request)
            raise exceptions.Throttled(
                wait=lockout.window_seconds(),
                detail="Too many failed attempts for this account. Try again later.",
            )

        # Django's authenticate() already emits user_login_failed on failure,
        # and ModelBackend rejects inactive users, so both cases are covered.
        user = authenticate(
            request=request, username=email, password=attrs.get("password")
        )

        # One message for every failure — never reveal whether an account exists.
        if user is None:
            lockout.record_failure(lockout.ACCOUNT, email)
            raise serializers.ValidationError(
                {"detail": INVALID_CREDENTIALS}, code="invalid_credentials"
            )

        if two_factor.is_enabled(user):
            code = attrs.get("otp") or ""
            if not code:
                # Not a failure: the client asks for the code and sends both again.
                raise serializers.ValidationError(
                    {"detail": "Enter the code from your authenticator app."},
                    code="otp_required",
                )
            if not two_factor.verify(user, code):
                lockout.record_failure(lockout.ACCOUNT, email)
                log_login_failure(email, "invalid_otp", request)
                raise serializers.ValidationError(
                    {"detail": "That two-factor code is not valid."}, code="invalid_otp"
                )

        lockout.reset(lockout.ACCOUNT, email)
        self.user = user
        refresh = self.get_token(user)
        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
            "user": CurrentUserSerializer(user, context=self.context).data,
        }


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(help_text="The refresh token to blacklist.")


# ---------------------------------------------------------------------------
# Two-factor login
# ---------------------------------------------------------------------------
class CurrentPasswordMixin(serializers.Serializer):
    """Re-check the password before changing how the account logs in, so a
    borrowed unlocked session can't switch two-factor on or off."""

    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Incorrect password.")
        return value


class TwoFactorSetupSerializer(CurrentPasswordMixin):
    pass


class TwoFactorCodeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=32)


class TwoFactorDisableSerializer(CurrentPasswordMixin):
    code = serializers.CharField(max_length=32, help_text="App code or a recovery code.")

    def validate_code(self, value):
        if not two_factor.verify(self.context["request"].user, value):
            raise serializers.ValidationError("That two-factor code is not valid.")
        return value


class TwoFactorStatusSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    pending_setup = serializers.BooleanField()
    recovery_codes_left = serializers.IntegerField()


class TwoFactorSetupResponseSerializer(serializers.Serializer):
    secret = serializers.CharField(help_text="For typing into the app by hand.")
    otpauth_uri = serializers.CharField(help_text="Show as a QR code for the app to scan.")


class RecoveryCodesSerializer(serializers.Serializer):
    recovery_codes = serializers.ListField(
        child=serializers.CharField(),
        help_text="Each works once. Shown only now — store them somewhere safe.",
    )
