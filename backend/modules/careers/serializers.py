import json
from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from core.common.serializers import ensure_unique_in_organization, target_organization_id
from core.files.models import StoredFile
from modules.hr.models import ContractKind
from modules.staff.models import StaffMember

from .models import Candidacy, Interview, InterviewStatus, JobOffer, JobPosting, Recommendation, Vacancy


class OwnedSerializer(serializers.ModelSerializer):
    def own(self, value, label):
        if value is not None and value.organization_id != target_organization_id(self):
            raise serializers.ValidationError(f"Unknown {label}.")
        return value


class OwnedInput(serializers.Serializer):
    def own(self, value, label):
        if value is not None and value.organization_id != target_organization_id(self):
            raise serializers.ValidationError(f"Unknown {label}.")
        return value


# ---------------------------------------------------------------------------
# Vacancies
# ---------------------------------------------------------------------------
class VacancySerializer(OwnedSerializer):
    campus_name = serializers.CharField(source="campus.name", read_only=True)
    position_name = serializers.CharField(source="position.name", read_only=True, default=None)
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)
    candidates = serializers.IntegerField(read_only=True, default=None)

    class Meta:
        model = Vacancy
        fields = ["id", "organization", "campus", "campus_name", "application_type", "code", "title", "position",
                  "position_name", "department", "department_name", "staff_type", "contract_kind", "openings",
                  "description", "requirements", "min_experience_years", "salary_range", "opens_on", "closes_on",
                  "is_public", "resume_required", "status", "hired_count", "candidates", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "status", "hired_count", "created_at", "updated_at"]

    def validate_campus(self, value):
        value = self.own(value, "campus")
        if self.instance is not None and value.pk != self.instance.campus_id and self.instance.candidacies.exists():
            raise serializers.ValidationError("People have applied, so the campus can't change.")
        return value

    def validate_application_type(self, value):
        value = self.own(value, "application form")
        if value.kind != "job":
            raise serializers.ValidationError("Pick a job application form.")
        if (self.instance is not None and value.pk != self.instance.application_type_id
                and self.instance.candidacies.exists()):
            raise serializers.ValidationError("People have applied, so the form can't change.")
        return value

    def validate_position(self, value):
        return self.own(value, "position")

    def validate_department(self, value):
        return self.own(value, "department")

    def validate_code(self, value):
        value = value.lower()
        ensure_unique_in_organization(self, "code", value)
        return value

    def validate_requirements(self, value):
        if not isinstance(value, list) or not all(isinstance(v, str) and 0 < len(v) <= 300 for v in value):
            raise serializers.ValidationError("A list of requirement lines (up to 300 characters each).")
        if len(value) > 50:
            raise serializers.ValidationError("At most 50 lines.")
        return value

    def validate_openings(self, value):
        if value < 1:
            raise serializers.ValidationError("At least one.")
        if self.instance is not None and value < self.instance.hired_count:
            raise serializers.ValidationError(f"{self.instance.hired_count} already hired.")
        return value

    def validate(self, attrs):
        opens = attrs.get("opens_on", getattr(self.instance, "opens_on", None))
        closes = attrs.get("closes_on", getattr(self.instance, "closes_on", None))
        if opens and closes and closes < opens:
            raise serializers.ValidationError({"closes_on": "Can't close before it opens."})
        kind_type = attrs.get("application_type", getattr(self.instance, "application_type", None))
        campus = attrs.get("campus", getattr(self.instance, "campus", None))
        if kind_type is not None and kind_type.campus_id and campus is not None and kind_type.campus_id != campus.pk:
            raise serializers.ValidationError({"application_type": "That form is for another campus."})
        public = attrs.get("is_public", getattr(self.instance, "is_public", True))
        if public and kind_type is not None and not kind_type.is_public:
            raise serializers.ValidationError({"is_public": "A public vacancy needs a public job form."})
        return attrs


class PublicVacancySerializer(serializers.ModelSerializer):
    """What anyone may read about an open vacancy."""

    campus_name = serializers.CharField(source="campus.name", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)
    form_fields = serializers.JSONField(source="application_type.fields", read_only=True)

    class Meta:
        model = Vacancy
        fields = ["id", "code", "title", "campus", "campus_name", "department_name", "staff_type", "contract_kind",
                  "openings", "description", "requirements", "min_experience_years", "salary_range", "opens_on",
                  "closes_on", "resume_required", "form_fields"]
        read_only_fields = fields


class ApplySerializer(serializers.Serializer):
    data = serializers.JSONField(help_text="The candidate: names, contact, résumé, answers in 'extra'.")
    resume_file = serializers.PrimaryKeyRelatedField(queryset=StoredFile.objects.all(), required=False,
                                                     allow_null=True,
                                                     help_text="A file uploaded to /files/ with purpose resume.")

    def validate_resume_file(self, value):
        user = self.context["request"].user
        if value is not None and (value.organization_id != user.organization_id or value.uploaded_by_id != user.pk):
            raise serializers.ValidationError("Unknown file.")
        return value


class PublicApplySerializer(serializers.Serializer):
    """JSON, or multipart with ``data`` as a JSON string and ``resume`` the file."""

    data = serializers.JSONField()
    resume = serializers.FileField(required=False)

    def validate_data(self, value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                raise serializers.ValidationError("Not valid JSON.")
        if not isinstance(value, dict):
            raise serializers.ValidationError("An object.")
        return value


class ApplyResultSerializer(serializers.Serializer):
    number = serializers.CharField()
    token = serializers.CharField()
    status = serializers.CharField()


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------
class CandidacySerializer(serializers.ModelSerializer):
    vacancy_title = serializers.CharField(source="vacancy.title", read_only=True)
    application_number = serializers.CharField(source="application.number", read_only=True)
    status = serializers.CharField(source="application.status", read_only=True)
    step = serializers.IntegerField(source="application.step", read_only=True)
    resume_name = serializers.CharField(source="resume.name", read_only=True, default=None)

    class Meta:
        model = Candidacy
        fields = ["id", "organization", "vacancy", "vacancy_title", "application", "application_number", "status",
                  "step", "full_name", "email", "phone", "resume", "resume_name", "screening_score",
                  "screening_note", "hired_staff", "created_at", "updated_at"]
        read_only_fields = fields


class CandidacyDetailSerializer(CandidacySerializer):
    data = serializers.JSONField(source="application.data", read_only=True)

    class Meta(CandidacySerializer.Meta):
        fields = CandidacySerializer.Meta.fields + ["data"]
        read_only_fields = fields


class ScreenSerializer(serializers.Serializer):
    score = serializers.IntegerField(min_value=0, max_value=100, required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=5000)


# ---------------------------------------------------------------------------
# Interviews
# ---------------------------------------------------------------------------
class InterviewSerializer(serializers.ModelSerializer):
    candidate = serializers.CharField(source="candidacy.full_name", read_only=True)
    vacancy_title = serializers.CharField(source="candidacy.vacancy.title", read_only=True)
    panel_names = serializers.SerializerMethodField()

    class Meta:
        model = Interview
        fields = ["id", "organization", "candidacy", "candidate", "vacancy_title", "round", "scheduled_at",
                  "duration_minutes", "mode", "location", "panel", "panel_names", "status", "score",
                  "recommendation", "feedback", "created_at", "updated_at"]
        read_only_fields = fields

    def get_panel_names(self, obj) -> list[str]:
        return [s.full_name for s in obj.panel.all()]


class ScheduleInterviewSerializer(OwnedInput):
    candidacy = serializers.PrimaryKeyRelatedField(queryset=Candidacy.objects.select_related("vacancy",
                                                                                            "application"))
    round = serializers.IntegerField(min_value=1, max_value=20, default=1)
    scheduled_at = serializers.DateTimeField()
    duration_minutes = serializers.IntegerField(min_value=5, max_value=600, default=30)
    mode = serializers.ChoiceField(choices=Interview._meta.get_field("mode").choices, default="in_person")
    location = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    panel = serializers.PrimaryKeyRelatedField(queryset=StaffMember.objects.select_related("user"), many=True,
                                               required=False, default=list)

    def validate_candidacy(self, value):
        value = self.own(value, "candidate")
        view = self.context.get("view")
        if hasattr(view, "check_campus_allowed"):
            view.check_campus_allowed(value.vacancy.campus)
        return value

    def validate_panel(self, value):
        for staff in value:
            self.own(staff, "staff member")
            if staff.status == "left":
                raise serializers.ValidationError(f"{staff.full_name} has left.")
        return value


class RescheduleSerializer(serializers.Serializer):
    scheduled_at = serializers.DateTimeField()
    location = serializers.CharField(max_length=255, required=False)


class OutcomeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[InterviewStatus.COMPLETED, InterviewStatus.NO_SHOW])
    score = serializers.DecimalField(max_digits=4, decimal_places=1, min_value=Decimal("0"),
                                     max_value=Decimal("10"), required=False, allow_null=True)
    recommendation = serializers.ChoiceField(choices=Recommendation.choices, required=False, allow_blank=True,
                                             default="")
    feedback = serializers.CharField(required=False, allow_blank=True, default="", max_length=5000)

    def validate(self, attrs):
        if attrs["status"] == InterviewStatus.NO_SHOW:
            attrs["score"], attrs["recommendation"] = None, ""
        return attrs


class CareersReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------
class JobOfferSerializer(serializers.ModelSerializer):
    candidate = serializers.CharField(source="candidacy.full_name", read_only=True)
    vacancy_title = serializers.CharField(source="candidacy.vacancy.title", read_only=True)
    application_number = serializers.CharField(source="candidacy.application.number", read_only=True)

    class Meta:
        model = JobOffer
        fields = ["id", "organization", "candidacy", "candidate", "vacancy_title", "application_number",
                  "start_date", "contract_kind", "probation_ends_on", "contract_end_date", "salary_note", "terms",
                  "expires_on", "status", "responded_at", "response_note", "made_by", "created_at", "updated_at"]
        read_only_fields = fields


class MakeOfferSerializer(OwnedInput):
    candidacy = serializers.PrimaryKeyRelatedField(queryset=Candidacy.objects.select_related("vacancy"))
    start_date = serializers.DateField()
    contract_kind = serializers.ChoiceField(choices=ContractKind.choices, required=False)
    probation_ends_on = serializers.DateField(required=False, allow_null=True)
    contract_end_date = serializers.DateField(required=False, allow_null=True)
    salary_note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    terms = serializers.CharField(required=False, allow_blank=True, default="", max_length=5000)
    expires_on = serializers.DateField(required=False, allow_null=True)

    def validate_candidacy(self, value):
        value = self.own(value, "candidate")
        view = self.context.get("view")
        if hasattr(view, "check_campus_allowed"):
            view.check_campus_allowed(value.vacancy.campus)
        return value

    def validate(self, attrs):
        start = attrs["start_date"]
        attrs["contract_kind"] = attrs.get("contract_kind") or attrs["candidacy"].vacancy.contract_kind
        if attrs.get("probation_ends_on") and attrs["probation_ends_on"] < start:
            raise serializers.ValidationError({"probation_ends_on": "Must be after the start date."})
        end = attrs.get("contract_end_date")
        if end is not None and end < start:
            raise serializers.ValidationError({"contract_end_date": "Can't end before it starts."})
        if end is not None and attrs.get("probation_ends_on") and attrs["probation_ends_on"] > end:
            raise serializers.ValidationError({"probation_ends_on": "Must fall within the contract."})
        return attrs


class PublicOfferSerializer(serializers.ModelSerializer):
    """What the candidate sees of their offer, not who made it."""

    vacancy_title = serializers.CharField(source="candidacy.vacancy.title", read_only=True)

    class Meta:
        model = JobOffer
        fields = ["vacancy_title", "start_date", "contract_kind", "probation_ends_on", "contract_end_date",
                  "salary_note", "terms", "expires_on", "status", "responded_at"]
        read_only_fields = fields


class RespondOfferSerializer(serializers.Serializer):
    accept = serializers.BooleanField()
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class PublicRespondOfferSerializer(RespondOfferSerializer):
    number = serializers.CharField(max_length=30)
    token = serializers.CharField(max_length=100)


# ---------------------------------------------------------------------------
# Job board
# ---------------------------------------------------------------------------
class JobPostingSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobPosting
        fields = ["id", "organization", "title", "company", "location", "kind", "description", "how_to_apply",
                  "apply_url", "contact_email", "closes_on", "audience", "status", "posted_by", "reviewed_by",
                  "reviewed_at", "review_note", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "status", "posted_by", "reviewed_by", "reviewed_at",
                            "review_note", "created_at", "updated_at"]

    def validate_closes_on(self, value):
        if value is not None and value < timezone.localdate():
            raise serializers.ValidationError("Can't close in the past.")
        return value

    def validate(self, attrs):
        if self.instance is not None and self.instance.status in ("rejected", "closed"):
            raise serializers.ValidationError("A closed or rejected posting can't change.")
        how = attrs.get("how_to_apply", getattr(self.instance, "how_to_apply", ""))
        url = attrs.get("apply_url", getattr(self.instance, "apply_url", ""))
        email = attrs.get("contact_email", getattr(self.instance, "contact_email", ""))
        if not (how or url or email):
            raise serializers.ValidationError("Say how to apply: a link, an email, or instructions.")
        return attrs


class ReviewPostingSerializer(serializers.Serializer):
    approve = serializers.BooleanField()
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

