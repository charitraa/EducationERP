import zoneinfo

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from core.accounts.models import User
from core.organizations.models import Organization, code_validator

from . import disposable, services
from .models import SignupRequest

CAPTCHA_HELP = "The token from the CAPTCHA widget; see GET /signup/config/. Not needed while CAPTCHA is off."


class CaptchaSerializer(serializers.Serializer):
    captcha_token = serializers.CharField(required=False, allow_blank=True, max_length=4096,
                                          write_only=True, help_text=CAPTCHA_HELP)


class SignupConfigSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    requires_approval = serializers.BooleanField()
    captcha_provider = serializers.ChoiceField(choices=["off", "turnstile", "hcaptcha", "recaptcha"])
    captcha_site_key = serializers.CharField()
    organization_types = serializers.ListField(child=serializers.DictField())


class StartSignupSerializer(CaptchaSerializer):
    organization_name = serializers.CharField(max_length=200)
    organization_code = serializers.CharField(
        min_length=2, max_length=50, validators=[code_validator],
        help_text="Short permanent identifier, e.g. 'central-college'. Check it with GET /signup/check-code/.")
    organization_type = serializers.ChoiceField(choices=Organization.Type.choices, default=Organization.Type.COLLEGE)
    timezone = serializers.CharField(max_length=64, default="UTC", help_text='e.g. "Asia/Kathmandu".')
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    email = serializers.EmailField(max_length=254)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    password = serializers.CharField(write_only=True, max_length=128, style={"input_type": "password"})

    def validate_organization_code(self, value):
        value = value.lower()
        reason = services.code_unavailable_reason(value)
        if reason == "reserved":
            raise serializers.ValidationError("This code is reserved. Choose another one.")
        if reason == "taken":
            raise serializers.ValidationError("This code is taken. Choose another one.")
        return value

    def validate_timezone(self, value):
        if value not in zoneinfo.available_timezones():
            raise serializers.ValidationError("Unknown time zone.")
        return value

    def validate_email(self, value):
        value = value.lower().strip()
        if disposable.is_disposable(value):
            raise serializers.ValidationError("Use a permanent email address, not a throwaway one.",
                                              code="disposable_email")
        return value

    def validate(self, attrs):
        # Checked against the person's own name and email, as for any user.
        candidate = User(email=attrs["email"], first_name=attrs["first_name"], last_name=attrs.get("last_name", ""))
        try:
            validate_password(attrs["password"], candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        return attrs


class ResendSerializer(CaptchaSerializer):
    email = serializers.EmailField(max_length=254)


class VerifySerializer(serializers.Serializer):
    token = serializers.CharField(max_length=200)


class CheckCodeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50)


class CodeAvailabilitySerializer(serializers.Serializer):
    code = serializers.CharField()
    available = serializers.BooleanField()
    reason = serializers.ChoiceField(choices=["invalid", "reserved", "taken"], allow_null=True)


class AcceptedSerializer(serializers.Serializer):
    detail = serializers.CharField()


class VerifiedSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=SignupRequest.Status.choices)
    organization = serializers.DictField(allow_null=True)
    refresh = serializers.CharField(required=False, help_text="Signed in at once when the organization is created.")
    access = serializers.CharField(required=False)
    user = serializers.DictField(required=False)


class SignupRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = SignupRequest
        fields = ["id", "organization_name", "organization_code", "organization_type", "timezone", "admin_email",
                  "admin_first_name", "admin_last_name", "admin_phone", "status", "token_expires_at",
                  "verified_at", "decided_at", "decided_by", "rejection_reason", "organization", "ip_address",
                  "created_at", "updated_at"]
        read_only_fields = fields


class RejectSignupSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField(max_length=64)
    token = serializers.CharField(max_length=100)
    new_password = serializers.CharField(write_only=True, max_length=128, style={"input_type": "password"})
