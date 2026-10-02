"""What each kind of application asks for, and what approving it does.

Each kind has:

* ``subject``: who it is about. ``student`` (applied for by the student, a
  parent, or the office), ``staff`` (by the staff member or the office),
  ``any`` (either, or nobody for a general request), or ``none``
  (admission: the applicant isn't anyone yet).
* a data serializer, validated at submission and resubmission. Ids are
  checked against the organization and stored as plain numbers.
* ``final_permission``: the permission the type's last step must name.
  The handler acts through the owning module's service as the final
  approver, whose own checks (campus, not-your-own-leave) still apply.
* ``fulfil(application, decision, by)``, run inside the approval's
  transaction. It returns ``(outcome, label)``; a ``ServiceError`` refuses
  the approval.
* optionally a decision serializer: what the final approver must supply
  (a hostel bed).
"""
from dataclasses import dataclass
from datetime import date

from django.utils import timezone
from rest_framework import serializers

from core.common.choices import Gender
from core.common.exceptions import ConflictError

EXTRA_TYPES = ("text", "textarea", "number", "date", "choice", "boolean")


def _org_row(model, pk, organization_id, label):
    row = model.objects.filter(pk=pk, organization_id=organization_id).first()
    if row is None:
        raise serializers.ValidationError(f"Unknown {label}.")
    return row


class KindData(serializers.Serializer):
    """Base: ``context`` carries organization_id, campus, student and staff."""

    def org(self):
        return self.context["organization_id"]


# ---------------------------------------------------------------------------
# Data per kind
# ---------------------------------------------------------------------------
class AdmissionData(KindData):
    first_name = serializers.CharField(max_length=150)
    middle_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    last_name = serializers.CharField(max_length=150)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    gender = serializers.ChoiceField(choices=Gender.choices, required=False, allow_blank=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    address = serializers.CharField(required=False, allow_blank=True, default="")
    previous_school = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    applying_for = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    guardian_first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    guardian_last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    guardian_relationship = serializers.ChoiceField(choices=["father", "mother", "guardian", "other"],
                                                    required=False, allow_blank=True, default="")
    guardian_phone = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    guardian_email = serializers.EmailField(required=False, allow_blank=True, default="")

    def validate_date_of_birth(self, value):
        if value is not None and value >= timezone.localdate():
            raise serializers.ValidationError("Must be in the past.")
        return value


class LeaveData(KindData):
    leave_type = serializers.IntegerField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    half_day = serializers.BooleanField(default=False)
    reason = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_leave_type(self, value):
        from modules.hr.models import LeaveType

        if not _org_row(LeaveType, value, self.org(), "leave type").is_active:
            raise serializers.ValidationError("This leave type is no longer in use.")
        return value

    def validate(self, attrs):
        if attrs["end_date"] < attrs["start_date"]:
            raise serializers.ValidationError({"end_date": "Can't end before it starts."})
        return attrs


class ScholarshipData(KindData):
    scholarship = serializers.IntegerField()
    reason = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_scholarship(self, value):
        from modules.finance.models import Scholarship

        if not _org_row(Scholarship, value, self.org(), "scholarship").is_active:
            raise serializers.ValidationError("This scholarship isn't offered any more.")
        return value


class HostelData(KindData):
    building = serializers.IntegerField(required=False, allow_null=True)
    room_type = serializers.IntegerField(required=False, allow_null=True)
    start_date = serializers.DateField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_building(self, value):
        from modules.hostel.models import Building

        if value is not None:
            _org_row(Building, value, self.org(), "building")
        return value

    def validate_room_type(self, value):
        from modules.hostel.models import RoomType

        if value is not None:
            _org_row(RoomType, value, self.org(), "room type")
        return value


class HostelDecision(KindData):
    bed = serializers.IntegerField(help_text="The bed to reserve for the student.")

    def validate_bed(self, value):
        from modules.hostel.models import Bed

        _org_row(Bed, value, self.org(), "bed")
        return value


class TransportData(KindData):
    route = serializers.IntegerField()
    stop = serializers.IntegerField()
    direction = serializers.ChoiceField(choices=["both", "pickup", "drop"], default="both")
    start_date = serializers.DateField(required=False, allow_null=True)

    def validate(self, attrs):
        from modules.transport.models import Route, Stop

        route = _org_row(Route, attrs["route"], self.org(), "route")
        stop = _org_row(Stop, attrs["stop"], self.org(), "stop")
        if stop.route_id != route.pk:
            raise serializers.ValidationError({"stop": "That stop isn't on this route."})
        if not route.is_active:
            raise serializers.ValidationError({"route": "This route isn't running."})
        return attrs


class EventData(KindData):
    event = serializers.IntegerField()
    note = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_event(self, value):
        from modules.events.models import Event

        if _org_row(Event, value, self.org(), "event").status != "published":
            raise serializers.ValidationError("This event isn't open.")
        return value


class CertificateData(KindData):
    purpose = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class GeneralData(KindData):
    subject = serializers.CharField(max_length=200)
    details = serializers.CharField(required=False, allow_blank=True, default="")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def _start(value):
    return date.fromisoformat(value) if value else timezone.localdate()


def fulfil_admission(application, decision, by):
    from modules.admissions.services import approve_admission, create_admission

    data = dict(application.data)
    data.pop("extra", None)
    if data.get("date_of_birth"):
        data["date_of_birth"] = date.fromisoformat(data["date_of_birth"])
    admission = create_admission(organization_id=application.organization_id, campus=application.campus,
                                 application_number=application.number,
                                 applied_on=timezone.localtime(application.submitted_at).date(), by=by, **data)
    approve_admission(admission=admission, note=f"Approved through application {application.number}", by=by)
    return ({"type": "admissions.admission", "id": admission.pk},
            f"Admission {admission.application_number} approved; enroll it from Admissions.")


def fulfil_leave(application, decision, by):
    from modules.hr.models import LeaveType
    from modules.hr.services import apply_leave, approve_leave

    data = application.data
    request = apply_leave(staff=application.staff, leave_type=LeaveType.objects.get(pk=data["leave_type"]),
                          start_date=date.fromisoformat(data["start_date"]),
                          end_date=date.fromisoformat(data["end_date"]), half_day=data.get("half_day", False),
                          reason=data.get("reason", ""), by=application.applicant, notify_approvers=False)
    approve_leave(request, by=by, note=f"Approved through application {application.number}")
    return ({"type": "hr.leave_request", "id": request.pk},
            f"{request.days} day(s) of {request.leave_type.name} approved.")


def fulfil_scholarship(application, decision, by):
    from modules.finance.models import Scholarship, StudentScholarship
    from modules.finance.services import grant_scholarship

    scholarship = Scholarship.objects.get(pk=application.data["scholarship"])
    if StudentScholarship.objects.on().filter(student=application.student, scholarship=scholarship).exists():
        raise ConflictError("The student already holds this scholarship.", code="already_granted")
    grant = grant_scholarship(student=application.student, scholarship=scholarship,
                              started_on=timezone.localdate(), reason=application.data.get("reason", ""), by=by)
    return ({"type": "finance.student_scholarship", "id": grant.pk}, f"{scholarship.name} granted.")


def fulfil_hostel(application, decision, by):
    from modules.hostel.models import Bed
    from modules.hostel.services import allocate_hostel_room

    bed = Bed.objects.select_related("room__building").get(pk=decision["bed"])
    allocation = allocate_hostel_room(bed=bed, student=application.student,
                                      start_date=_start(application.data.get("start_date")),
                                      note=f"Application {application.number}", by=by)
    return ({"type": "hostel.allocation", "id": allocation.pk},
            f"Bed {bed} reserved from {allocation.start_date}.")


def fulfil_transport(application, decision, by):
    from modules.transport.models import Route, Stop
    from modules.transport.services import assign_rider

    data = application.data
    assignment = assign_rider(route=Route.objects.get(pk=data["route"]), stop=Stop.objects.get(pk=data["stop"]),
                              student=application.student, direction=data.get("direction", "both"),
                              start_date=_start(data.get("start_date")), by=by)
    return ({"type": "transport.assignment", "id": assignment.pk},
            f"{assignment.route.name} from {assignment.stop.name}, starting {assignment.start_date}.")


def fulfil_event(application, decision, by):
    from modules.events.models import Event
    from modules.events.services import decide_registration, register

    event = Event.objects.get(pk=application.data["event"])
    registration = register(event, application.student, note=application.data.get("note", ""), by=by)
    if registration.status == "pending":
        registration = decide_registration(registration, True, note=f"Application {application.number}", by=by)
    return ({"type": "events.registration", "id": registration.pk}, f"Registered for {event.name}.")


def fulfil_certificate(application, decision, by):
    from .services import issue_certificate

    certificate = issue_certificate(
        student=application.student,
        title=application.application_type.certificate_title or application.application_type.name,
        purpose=application.data.get("purpose", ""), application=application, by=by)
    return ({"type": "applications.certificate", "id": certificate.pk}, f"Certificate {certificate.number} issued.")


def fulfil_nothing(application, decision, by):
    return {}, "Approved."


@dataclass(frozen=True)
class KindSpec:
    subject: str
    data: type
    fulfil: object
    final_permission: str | None = None
    decision: type | None = None


KINDS = {
    "admission": KindSpec("none", AdmissionData, fulfil_admission, "admissions.review"),
    "leave": KindSpec("staff", LeaveData, fulfil_leave, "hr.approve_leave"),
    "scholarship": KindSpec("student", ScholarshipData, fulfil_scholarship, "finance.manage"),
    "hostel": KindSpec("student", HostelData, fulfil_hostel, "hostel.manage", HostelDecision),
    "transport": KindSpec("student", TransportData, fulfil_transport, "transport.manage"),
    "event": KindSpec("student", EventData, fulfil_event, "events.manage"),
    "certificate": KindSpec("student", CertificateData, fulfil_certificate, "applications.certify"),
    "general": KindSpec("any", GeneralData, fulfil_nothing, None),
}


# ---------------------------------------------------------------------------
# Extra questions
# ---------------------------------------------------------------------------
def check_field_definitions(fields) -> list[dict]:
    """Validate a type's extra questions as configured by the office."""
    if not isinstance(fields, list):
        raise serializers.ValidationError("A list of questions.")
    seen, clean = set(), []
    for n, item in enumerate(fields, 1):
        if not isinstance(item, dict):
            raise serializers.ValidationError(f"Question {n}: an object.")
        name, kind = str(item.get("name", "")).strip(), item.get("type", "text")
        if not name.replace("_", "").isalnum() or not name[:1].isalpha():
            raise serializers.ValidationError(f"Question {n}: name must be letters, digits and _.")
        if name in seen:
            raise serializers.ValidationError(f"Question {n}: '{name}' is used twice.")
        if kind not in EXTRA_TYPES:
            raise serializers.ValidationError(f"Question {n}: type is one of {', '.join(EXTRA_TYPES)}.")
        choices = item.get("choices") or []
        if kind == "choice" and (not isinstance(choices, list) or not choices
                                 or not all(isinstance(c, str) and c for c in choices)):
            raise serializers.ValidationError(f"Question {n}: a choice question needs a list of choices.")
        seen.add(name)
        clean.append({"name": name, "label": str(item.get("label") or name)[:200], "type": kind,
                      "required": bool(item.get("required", False)),
                      "choices": choices if kind == "choice" else []})
    return clean


def clean_extra(definitions, raw) -> dict:
    """The applicant's answers to the extra questions."""
    raw = raw or {}
    if not isinstance(raw, dict):
        raise serializers.ValidationError({"extra": "An object of answers."})
    errors, answers = {}, {}
    for q in definitions:
        value = raw.get(q["name"])
        if value in (None, ""):
            if q["required"]:
                errors[q["name"]] = "This question is required."
            continue
        try:
            if q["type"] in ("text", "textarea"):
                value = str(value)[:5000]
            elif q["type"] == "number":
                value = float(value)
            elif q["type"] == "date":
                value = date.fromisoformat(str(value)).isoformat()
            elif q["type"] == "boolean":
                if not isinstance(value, bool):
                    raise ValueError
            elif q["type"] == "choice" and value not in q["choices"]:
                raise ValueError
        except (TypeError, ValueError):
            errors[q["name"]] = f"Not a valid {q['type']}."
            continue
        answers[q["name"]] = value
    unknown = set(raw) - {q["name"] for q in definitions}
    for name in sorted(unknown):
        errors[name] = "Not a question on this form."
    if errors:
        raise serializers.ValidationError({"extra": errors})
    return answers


def clean_data(application_type, raw, *, campus, student=None, staff=None) -> dict:
    """Validate ``raw`` for the type's kind; returns JSON-safe data."""
    if not isinstance(raw, dict):
        raise serializers.ValidationError({"data": "An object."})
    spec = KINDS[application_type.kind]
    serializer = spec.data(data=raw, context={"organization_id": application_type.organization_id,
                                              "campus": campus, "student": student, "staff": staff})
    if not serializer.is_valid():
        raise serializers.ValidationError({"data": serializer.errors})
    data = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in serializer.validated_data.items()}
    data["extra"] = clean_extra(application_type.fields, raw.get("extra"))
    return data


def clean_decision(application, raw) -> dict:
    spec = KINDS[application.application_type.kind]
    if spec.decision is None:
        return {}
    serializer = spec.decision(data=raw or {}, context={"organization_id": application.organization_id})
    if not serializer.is_valid():
        raise serializers.ValidationError({"decision": serializer.errors})
    return dict(serializer.validated_data)

