from django.contrib.auth import authenticate
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from core.accounts.serializers import CurrentUserSerializer


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

    def validate(self, attrs):
        """Authenticate once and build the token pair.

        Deliberately does not call ``super().validate()``: that would run
        ``authenticate()`` a second time and emit a duplicate
        ``user_login_failed`` signal into the audit trail.
        """
        request = self.context.get("request")
        email = (attrs.get(self.username_field) or "").lower().strip()

        # Django's authenticate() already emits user_login_failed on failure,
        # and ModelBackend rejects inactive users, so both cases are covered.
        user = authenticate(
            request=request, username=email, password=attrs.get("password")
        )

        # One message for every failure — never reveal whether an account exists.
        if user is None:
            raise serializers.ValidationError(
                {"detail": "Invalid credentials or inactive account."},
                code="invalid_credentials",
            )

        self.user = user
        refresh = self.get_token(user)
        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
            "user": CurrentUserSerializer(user, context=self.context).data,
        }


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(help_text="The refresh token to blacklist.")
