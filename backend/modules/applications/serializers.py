from django.db import transaction
from rest_framework import serializers

from core.common.exceptions import ServiceError
from core.common.serializers import ensure_unique_in_organization, target_organization_id
from core.organizations.models import Campus
from modules.staff.models import StaffMember
from modules.students.models import Student

from . import services
from .kinds import KINDS, check_field_definitions
from .models import PUBLIC_KINDS, Application, ApplicationEvent, ApplicationType, ApprovalStep, Certificate


class OwnedInput(serializers.Serializer):
    def own(self, value, label):
        if value is not None and value.organization_id != target_organization_id(self):
            raise serializers.ValidationError(f"Unknown {label}.")
        return value


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
class ApprovalStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApprovalStep
        fields = ["sequence", "name", "permission"]
        read_only_fields = ["sequence"]


class ApplicationTypeSerializer(serializers.ModelSerializer):
    steps = ApprovalStepSerializer(many=True)
    campus_name = serializers.CharField(source="campus.name", read_only=True, default=None)
    decision_fields = serializers.SerializerMethodField()

    class Meta:
        model = ApplicationType
        fields = ["id", "organization", "code", "name", "kind", "description", "campus", "campus_name",
                  "is_public", "is_active", "fields", "certificate_title", "steps", "decision_fields",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def get_decision_fields(self, obj) -> list[str]:
        decision = KINDS[obj.kind].decision
        return list(decision().fields) if decision else []

    def validate_campus(self, value):
        if value is not None and value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown campus.")
        return value

    def validate_code(self, value):
        value = value.lower()
        ensure_unique_in_organization(self, "code", value)
        return value

    def validate_fields(self, value):
        return check_field_definitions(value)

    def validate_kind(self, value):
        if self.instance is not None and value != self.instance.kind and self.instance.applications.exists():
            raise serializers.ValidationError("Applications of this type exist, so its kind can't change.")
        return value

    def validate(self, attrs):
        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        if attrs.get("is_public", getattr(self.instance, "is_public", False)) and kind not in PUBLIC_KINDS:
            raise serializers.ValidationError({"is_public": "Only an admission or job form can be public."})
        if "steps" in attrs or self.instance is None or "kind" in attrs:
            steps = attrs.get("steps")
            if steps is None:
                steps = [{"name": s.name, "permission": s.permission} for s in self.instance.steps.all()]
            try:
                services.check_steps(kind, steps)
            except ServiceError as exc:
                raise serializers.ValidationError({"steps": str(exc.detail)})
        return attrs

    def create(self, validated_data):
        steps = validated_data.pop("steps")
        with transaction.atomic():
            application_type = super().create(validated_data)
            services.replace_steps(application_type, steps)
        return application_type

    def update(self, instance, validated_data):
        steps = validated_data.pop("steps", None)
        with transaction.atomic():
            instance = super().update(instance, validated_data)
            if steps is not None:
                services.replace_steps(instance, steps)
        return instance


class AvailableTypeSerializer(serializers.ModelSerializer):
    """What an applicant sees of a form: no internal steps or permissions."""

    steps = serializers.SerializerMethodField()

    class Meta:
        model = ApplicationType
        fields = ["id", "code", "name", "kind", "description", "campus", "fields", "steps"]
        read_only_fields = fields

    def get_steps(self, obj) -> list[str]:
        return [s.name for s in obj.steps.all()]


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
class ApplicationEventSerializer(serializers.ModelSerializer):
    by_name = serializers.SerializerMethodField()

    class Meta:
        model = ApplicationEvent
        fields = ["action", "step", "step_name", "by", "by_name", "note", "at"]
        read_only_fields = fields

    def get_by_name(self, obj) -> str:
        return obj.by.get_full_name() if obj.by else ""


class ApplicationSerializer(serializers.ModelSerializer):
    type_name = serializers.CharField(source="application_type.name", read_only=True)
    kind = serializers.CharField(source="application_type.kind", read_only=True)
    campus_name = serializers.CharField(source="campus.name", read_only=True)
    subject_name = serializers.CharField(read_only=True)
    step_name = serializers.SerializerMethodField()
    events = ApplicationEventSerializer(many=True, read_only=True)

    class Meta:
        model = Application
        fields = ["id", "organization", "application_type", "type_name", "kind", "campus", "campus_name", "number",
                  "status", "step", "step_name", "applicant", "student", "staff", "subject_name", "contact_name",
                  "contact_email", "contact_phone", "data", "submitted_at", "decided_at", "outcome",
                  "outcome_label", "events", "created_at", "updated_at"]
        read_only_fields = fields

    def get_step_name(self, obj) -> str | None:
        if obj.status != "in_review":
            return None
        step = next((s for s in obj.application_type.steps.all() if s.sequence == obj.step), None)
        return step.name if step else None


class ApplicationListSerializer(ApplicationSerializer):
    class Meta(ApplicationSerializer.Meta):
        fields = [f for f in ApplicationSerializer.Meta.fields if f not in ("events", "data")]
        read_only_fields = fields


class SubmitApplicationSerializer(OwnedInput):
    application_type = serializers.PrimaryKeyRelatedField(
        queryset=ApplicationType.objects.prefetch_related("steps"))
    campus = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all(), required=False, allow_null=True,
                                                help_text="Admission and general requests with no subject.")
    student = serializers.PrimaryKeyRelatedField(queryset=Student.objects.select_related("campus"),
                                                 required=False, allow_null=True)
    staff = serializers.PrimaryKeyRelatedField(queryset=StaffMember.objects.select_related("campus"),
                                               required=False, allow_null=True)
    data = serializers.JSONField()

    def validate_application_type(self, value):
        return self.own(value, "application type")

    def validate_campus(self, value):
        return self.own(value, "campus")

    def validate_student(self, value):
        return self.own(value, "student")

    def validate_staff(self, value):
        return self.own(value, "staff member")


class DecideSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="")
    decision = serializers.JSONField(required=False, default=dict,
                                     help_text="What the final step must supply, e.g. {\"bed\": 12} for hostel.")


class NoteSerializer(serializers.Serializer):
    note = serializers.CharField()


class ResubmitSerializer(serializers.Serializer):
    data = serializers.JSONField()
    note = serializers.CharField(required=False, allow_blank=True, default="")


class WithdrawSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="")


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------
class CertificateSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    is_valid = serializers.SerializerMethodField()

    class Meta:
        model = Certificate
        fields = ["id", "organization", "student", "student_name", "application", "title", "number", "purpose",
                  "issued_on", "contents", "issued_by", "is_valid", "revoked_at", "revoked_reason", "revoked_by",
                  "created_at", "updated_at"]
        read_only_fields = fields

    def get_is_valid(self, obj) -> bool:
        return obj.revoked_at is None


class IssueCertificateSerializer(OwnedInput):
    student = serializers.PrimaryKeyRelatedField(queryset=Student.objects.select_related("campus"))
    title = serializers.CharField(max_length=150)
    purpose = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate_student(self, value):
        value = self.own(value, "student")
        view = self.context.get("view")
        if hasattr(view, "check_campus_allowed"):
            view.check_campus_allowed(value.campus)
        return value


class RevokeSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


# ---------------------------------------------------------------------------
# Public (no account)
# ---------------------------------------------------------------------------
class ContactSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if not attrs["email"] and not attrs["phone"]:
            raise serializers.ValidationError("Give an email or a phone number to be reached at.")
        return attrs


class PublicSubmitSerializer(serializers.Serializer):
    application_type = serializers.IntegerField()
    campus = serializers.IntegerField()
    contact = ContactSerializer()
    data = serializers.JSONField()
    captcha_token = serializers.CharField(required=False, allow_blank=True, max_length=4096, write_only=True,
                                          help_text="The CAPTCHA widget's token (GET /signup/config/ names the "
                                                    "widget). Not needed while CAPTCHA is off.")


class PublicLookupSerializer(serializers.Serializer):
    number = serializers.CharField(max_length=30)
    token = serializers.CharField(max_length=100)


class PublicResubmitSerializer(PublicLookupSerializer):
    data = serializers.JSONField()


class PublicStatusSerializer(serializers.ModelSerializer):
    """What a public applicant may see: the progress, not who decided."""

    type_name = serializers.CharField(source="application_type.name", read_only=True)
    history = serializers.SerializerMethodField()

    class Meta:
        model = Application
        fields = ["number", "type_name", "status", "submitted_at", "decided_at", "outcome_label", "data", "history"]
        read_only_fields = fields

    def get_history(self, obj) -> list[dict]:
        # Notes are shown only when they're addressed to the applicant.
        return [{"action": e.action, "step_name": e.step_name, "at": e.at,
                 "note": e.note if e.action in ("returned", "rejected") else ""} for e in obj.events.all()]
