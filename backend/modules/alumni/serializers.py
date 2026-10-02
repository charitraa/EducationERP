from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from core.common.serializers import ensure_unique_in_organization, target_organization_id, validate_linked_user
from core.organizations.models import Campus
from modules.academics.models import Section
from modules.finance.models import PaymentMethod
from modules.students.models import Student

from . import services
from .models import (
    Achievement,
    AlumniEvent,
    AlumniProfile,
    Campaign,
    Donation,
    DonationRefund,
    Employment,
    HigherStudy,
    Mentorship,
    Rsvp,
    RsvpResponse,
)


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


def _in_scope(serializer, campus):
    view = serializer.context.get("view")
    if campus is not None and hasattr(view, "check_campus_allowed"):
        view.check_campus_allowed(campus)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------
SELF_EDITABLE = ["email", "phone", "address", "city", "country", "linkedin_url", "website_url", "bio",
                 "directory_visible", "is_mentor", "mentor_topics", "mentor_capacity"]


class CurrentJobMixin(serializers.Serializer):
    current_job = serializers.SerializerMethodField()

    def get_current_job(self, obj) -> dict | None:
        jobs = [e for e in obj.employments.all() if e.end_date is None and e.deleted_at is None]
        if not jobs:
            return None
        job = max(jobs, key=lambda e: e.start_date)
        return {"employer": job.employer, "title": job.title, "location": job.location}


class AlumniProfileSerializer(OwnedSerializer, CurrentJobMixin):
    campus_name = serializers.CharField(source="campus.name", read_only=True)
    full_name = serializers.CharField(read_only=True)
    student_number = serializers.CharField(source="student.student_number", read_only=True, default=None)

    class Meta:
        model = AlumniProfile
        fields = ["id", "organization", "campus", "campus_name", "student", "student_number", "user", "first_name",
                  "middle_name", "last_name", "full_name", "gender", "date_of_birth", "email", "phone", "address",
                  "city", "country", "program", "program_name", "level", "section_name", "academic_year",
                  "graduated_on", "linkedin_url", "website_url", "bio", "directory_visible", "is_mentor",
                  "mentor_topics", "mentor_capacity", "current_job", "created_at", "updated_at"]
        # The student link is made by graduation, never by hand.
        read_only_fields = ["id", "organization", "student", "created_at", "updated_at"]

    def validate_campus(self, value):
        value = self.own(value, "campus")
        if self.instance is not None and self.instance.student_id and value.pk != self.instance.campus_id:
            raise serializers.ValidationError("A graduate's campus is where they studied; it can't change.")
        return value

    def validate_program(self, value):
        return self.own(value, "program")

    def validate_user(self, value):
        value = validate_linked_user(self, value)
        if value is not None and value.user_type != value.Type.ALUMNI:
            raise serializers.ValidationError("Link an alumni login.")
        return value

    def validate_mentor_capacity(self, value):
        if value > 50:
            raise serializers.ValidationError("At most 50.")
        return value

    def validate(self, attrs):
        program = attrs.get("program")
        if program is not None and not attrs.get("program_name"):
            attrs["program_name"] = program.name
        graduated_on = attrs.get("graduated_on")
        if graduated_on is not None and graduated_on > timezone.localdate():
            raise serializers.ValidationError({"graduated_on": "Can't be in the future."})
        return attrs


class MyProfileSerializer(AlumniProfileSerializer):
    """What a graduate may change about themselves. Who they are and what
    they finished is the office's record."""

    class Meta(AlumniProfileSerializer.Meta):
        read_only_fields = [f for f in AlumniProfileSerializer.Meta.fields if f not in SELF_EDITABLE]


class DirectoryEntrySerializer(CurrentJobMixin, serializers.ModelSerializer):
    """What one graduate sees of another who chose to be listed."""

    full_name = serializers.CharField(read_only=True)
    campus_name = serializers.CharField(source="campus.name", read_only=True)

    class Meta:
        model = AlumniProfile
        fields = ["id", "full_name", "campus_name", "program_name", "academic_year", "city", "country",
                  "linkedin_url", "website_url", "bio", "is_mentor", "current_job"]
        read_only_fields = fields


class MentorCardSerializer(CurrentJobMixin, serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    places_left = serializers.SerializerMethodField()

    class Meta:
        model = AlumniProfile
        fields = ["id", "full_name", "program_name", "academic_year", "city", "country", "mentor_topics",
                  "linkedin_url", "bio", "places_left", "current_job"]
        read_only_fields = fields

    def get_places_left(self, obj) -> int:
        return max(obj.mentor_capacity - getattr(obj, "active_mentees", 0), 0)


class GraduateSerializer(OwnedInput):
    section = serializers.PrimaryKeyRelatedField(queryset=Section.objects.select_related("campus"),
                                                 required=False, allow_null=True,
                                                 help_text="Graduate everyone placed in this class on the date.")
    students = serializers.PrimaryKeyRelatedField(queryset=Student.objects.select_related("campus", "user"),
                                                  many=True, required=False)
    on_date = serializers.DateField(required=False, help_text="Default: today.")
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate_section(self, value):
        value = self.own(value, "class")
        _in_scope(self, getattr(value, "campus", None))
        return value

    def validate_students(self, value):
        for student in value:
            self.own(student, "student")
            _in_scope(self, student.campus)
        return value

    def validate(self, attrs):
        if bool(attrs.get("section")) == bool(attrs.get("students")):
            raise serializers.ValidationError("Give either a class or a list of students.")
        attrs["on_date"] = attrs.get("on_date") or timezone.localdate()
        if attrs["on_date"] > timezone.localdate():
            raise serializers.ValidationError({"on_date": "Can't graduate anyone in the future."})
        return attrs


class GraduationResultSerializer(serializers.Serializer):
    graduated = serializers.IntegerField()
    profiles = serializers.ListField(child=serializers.IntegerField())
    skipped = serializers.ListField(child=serializers.DictField())


# ---------------------------------------------------------------------------
# A graduate's own history
# ---------------------------------------------------------------------------
class ProfileRowSerializer(OwnedSerializer):
    """Employment, studies, achievements: ``profile`` defaults to the
    caller's own; the office names someone else's."""

    profile = serializers.PrimaryKeyRelatedField(queryset=AlumniProfile.objects.select_related("campus"),
                                                 required=False)
    profile_name = serializers.CharField(source="profile.full_name", read_only=True)

    def validate_profile(self, value):
        if self.instance is not None and value.pk != self.instance.profile_id:
            raise serializers.ValidationError("Can't move a record to another person.")
        return self.own(value, "alumnus")


class EmploymentSerializer(ProfileRowSerializer):
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = Employment
        fields = ["id", "organization", "profile", "profile_name", "employer", "title", "location", "start_date",
                  "end_date", "is_current", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def get_is_current(self, obj) -> bool:
        return obj.end_date is None

    def validate(self, attrs):
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start and start > timezone.localdate():
            raise serializers.ValidationError({"start_date": "Can't be in the future."})
        if end is not None and start and end < start:
            raise serializers.ValidationError({"end_date": "Can't end before it starts."})
        return attrs


class HigherStudySerializer(ProfileRowSerializer):
    class Meta:
        model = HigherStudy
        fields = ["id", "organization", "profile", "profile_name", "institution", "qualification", "field",
                  "country", "start_year", "end_year", "status", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate(self, attrs):
        start = attrs.get("start_year", getattr(self.instance, "start_year", None))
        end = attrs.get("end_year", getattr(self.instance, "end_year", None))
        this_year = timezone.localdate().year
        # Bikram Sambat years run about 57 ahead, so allow them too.
        if start is not None and not 1900 <= start <= this_year + 60:
            raise serializers.ValidationError({"start_year": "Not a plausible year."})
        if end is not None and start is not None and end < start:
            raise serializers.ValidationError({"end_year": "Can't end before it starts."})
        return attrs


class AchievementSerializer(ProfileRowSerializer):
    class Meta:
        model = Achievement
        fields = ["id", "organization", "profile", "profile_name", "title", "description", "achieved_on", "url",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_achieved_on(self, value):
        if value is not None and value > timezone.localdate():
            raise serializers.ValidationError("Can't be in the future.")
        return value


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
class AlumniEventSerializer(OwnedSerializer):
    campus_name = serializers.CharField(source="campus.name", read_only=True, default=None)
    places_taken = serializers.SerializerMethodField()

    class Meta:
        model = AlumniEvent
        fields = ["id", "organization", "campus", "campus_name", "title", "description", "starts_at", "ends_at",
                  "venue", "online_url", "capacity", "status", "places_taken", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "status", "created_at", "updated_at"]

    def get_places_taken(self, obj) -> int:
        return services.places_taken(obj)

    def validate_campus(self, value):
        return self.own(value, "campus")

    def validate(self, attrs):
        if self.instance is not None and self.instance.status == "cancelled":
            raise serializers.ValidationError("A cancelled event can't be changed.")
        starts = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        ends = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        if ends is not None and starts and ends < starts:
            raise serializers.ValidationError({"ends_at": "Can't end before it starts."})
        return attrs


class UpcomingEventSerializer(AlumniEventSerializer):
    my_response = serializers.SerializerMethodField()
    my_guests = serializers.SerializerMethodField()

    class Meta(AlumniEventSerializer.Meta):
        fields = ["id", "campus", "campus_name", "title", "description", "starts_at", "ends_at", "venue",
                  "online_url", "capacity", "places_taken", "my_response", "my_guests"]
        read_only_fields = fields

    def _mine(self, obj):
        profile = self.context.get("profile")
        return next((r for r in obj.rsvps.all() if r.profile_id == getattr(profile, "pk", None)), None)

    def get_my_response(self, obj) -> str | None:
        mine = self._mine(obj)
        return mine.response if mine else None

    def get_my_guests(self, obj) -> int:
        mine = self._mine(obj)
        return mine.guests if mine else 0


class RsvpSerializer(serializers.ModelSerializer):
    profile_name = serializers.CharField(source="profile.full_name", read_only=True)
    email = serializers.CharField(source="profile.email", read_only=True)

    class Meta:
        model = Rsvp
        fields = ["id", "event", "profile", "profile_name", "email", "response", "guests", "created_at",
                  "updated_at"]
        read_only_fields = fields


class RsvpInputSerializer(serializers.Serializer):
    response = serializers.ChoiceField(choices=RsvpResponse.choices)
    guests = serializers.IntegerField(min_value=0, max_value=10, default=0)


class CancelAlumniEventSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


# ---------------------------------------------------------------------------
# Mentoring
# ---------------------------------------------------------------------------
class MentorshipSerializer(serializers.ModelSerializer):
    mentor_name = serializers.CharField(source="mentor.full_name", read_only=True)
    mentee_name = serializers.SerializerMethodField()

    class Meta:
        model = Mentorship
        fields = ["id", "organization", "mentor", "mentor_name", "student", "mentee", "mentee_name", "topic",
                  "message", "status", "responded_at", "ended_at", "note", "created_at", "updated_at"]
        read_only_fields = fields

    def get_mentee_name(self, obj) -> str:
        return obj.student.full_name if obj.student_id else obj.mentee.full_name


class RequestMentorshipSerializer(OwnedInput):
    mentor = serializers.PrimaryKeyRelatedField(queryset=AlumniProfile.objects.all())
    topic = serializers.CharField(max_length=200)
    message = serializers.CharField(required=False, allow_blank=True, default="", max_length=2000)

    def validate_mentor(self, value):
        return self.own(value, "mentor")


class MentorshipNoteSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


# ---------------------------------------------------------------------------
# Giving
# ---------------------------------------------------------------------------
class CampaignSerializer(OwnedSerializer):
    campus_name = serializers.CharField(source="campus.name", read_only=True, default=None)

    class Meta:
        model = Campaign
        fields = ["id", "organization", "campus", "campus_name", "code", "name", "description", "goal_amount",
                  "starts_on", "ends_on", "is_active", "raised_amount", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "raised_amount", "created_at", "updated_at"]

    def validate_campus(self, value):
        value = self.own(value, "campus")
        if (self.instance is not None and value != self.instance.campus
                and self.instance.donations.exists()):
            raise serializers.ValidationError("Gifts are recorded against this campaign, so its campus can't change.")
        return value

    def validate_code(self, value):
        value = value.lower()
        ensure_unique_in_organization(self, "code", value)
        return value

    def validate_goal_amount(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError("Must be more than zero.")
        return value

    def validate(self, attrs):
        starts = attrs.get("starts_on", getattr(self.instance, "starts_on", None))
        ends = attrs.get("ends_on", getattr(self.instance, "ends_on", None))
        if ends is not None and starts and ends < starts:
            raise serializers.ValidationError({"ends_on": "Can't end before it starts."})
        return attrs


class OpenCampaignSerializer(serializers.ModelSerializer):
    class Meta:
        model = Campaign
        fields = ["id", "campus", "code", "name", "description", "goal_amount", "starts_on", "ends_on",
                  "raised_amount"]
        read_only_fields = fields


class DonationRefundSerializer(serializers.ModelSerializer):
    class Meta:
        model = DonationRefund
        fields = ["id", "donation", "amount", "refunded_on", "method", "reference", "reason", "recorded_by",
                  "created_at"]
        read_only_fields = fields


class DonationSerializer(serializers.ModelSerializer):
    campaign_name = serializers.CharField(source="campaign.name", read_only=True, default=None)
    refunds = DonationRefundSerializer(many=True, read_only=True)

    class Meta:
        model = Donation
        fields = ["id", "organization", "campus", "campaign", "campaign_name", "donor", "donor_name", "donor_email",
                  "donor_phone", "amount", "refunded_amount", "method", "received_on", "receipt_number",
                  "reference", "note", "is_anonymous", "recorded_by", "refunds", "created_at"]
        read_only_fields = fields


class RecordDonationSerializer(OwnedInput):
    campus = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all())
    campaign = serializers.PrimaryKeyRelatedField(queryset=Campaign.objects.all(), required=False,
                                                  allow_null=True)
    donor = serializers.PrimaryKeyRelatedField(queryset=AlumniProfile.objects.all(), required=False,
                                               allow_null=True)
    donor_name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    donor_email = serializers.EmailField(required=False, allow_blank=True, default="")
    donor_phone = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    method = serializers.ChoiceField(choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    received_on = serializers.DateField(required=False)
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    is_anonymous = serializers.BooleanField(default=False)

    def validate_campus(self, value):
        value = self.own(value, "campus")
        _in_scope(self, value)
        return value

    def validate_campaign(self, value):
        return self.own(value, "campaign")

    def validate_donor(self, value):
        return self.own(value, "alumnus")

    def validate(self, attrs):
        attrs["received_on"] = attrs.get("received_on") or timezone.localdate()
        if attrs["received_on"] > timezone.localdate():
            raise serializers.ValidationError({"received_on": "Can't be in the future."})
        if not attrs.get("donor") and not attrs.get("donor_name"):
            raise serializers.ValidationError({"donor_name": "Name the donor, or pick an alumnus."})
        return attrs


class RefundDonationSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    reason = serializers.CharField(max_length=255)
    refunded_on = serializers.DateField(required=False)
    method = serializers.ChoiceField(choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")

    def validate_refunded_on(self, value):
        if value is not None and value > timezone.localdate():
            raise serializers.ValidationError("Can't be in the future.")
        return value

