"""The application workflow: submit, decide step by step, send back,
resubmit, withdraw — and certificates.

Every decision locks the application row first (``select_for_update``), so
two approvers acting at once can't both move it on, and the final approval
runs the kind's handler in the same transaction: if the handler refuses
(the bed was taken, the bus is full), nothing about the application changes.
"""
import hashlib
import hmac
import secrets

from django.db import transaction
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, PermissionDeniedError, ServiceError
from core.permissions.models import Permission
from core.permissions.selectors import campus_ids_with_permission, users_holding
from modules.notifications.services import notify
from modules.parents.selectors import links_for_parent, parent_for_user
from modules.students.selectors import get_current_enrollment

from .kinds import KINDS, clean_data, clean_decision
from .models import OPEN, Application, ApplicationEvent, ApprovalStep, Certificate, EventAction, Status

MODULE = "applications"
VIEW = "applications.view"
MANAGE = "applications.manage"
CERTIFY = "applications.certify"


def holds(user, code: str, campus_id) -> bool:
    if user is None or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    campus_ids = campus_ids_with_permission(user, code)
    return campus_ids is None or campus_id in campus_ids


def _next_number(organization_id: int, prefix: str, model) -> str:
    from core.organizations.models import Organization

    with transaction.atomic():
        Organization.objects.select_for_update().get(pk=organization_id)
        count = model.objects.filter(organization_id=organization_id).count()
        return f"{prefix}{count + 1:06d}"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Types and their steps
# ---------------------------------------------------------------------------
def check_steps(kind: str, steps: list[dict]) -> None:
    if not steps:
        raise ServiceError("Add at least one approval step.", code="no_steps")
    codes = [s["permission"] for s in steps]
    known = set(Permission.objects.filter(code__in=codes).values_list("code", flat=True))
    unknown = sorted(set(codes) - known)
    if unknown:
        raise ServiceError(f"Unknown permission(s): {', '.join(unknown)}.", code="unknown_permission")
    final = KINDS[kind].final_permission
    if final and codes[-1] != final:
        raise ServiceError(f"The last step of a {kind} application must be decided by holders of {final}, "
                           "because approving it acts with that permission.", code="wrong_final_permission")


def replace_steps(application_type, steps: list[dict]) -> None:
    """Swap the type's chain for ``steps`` (``[{name, permission}]``, in order)."""
    check_steps(application_type.kind, steps)
    if Application.objects.filter(application_type=application_type, status__in=OPEN).exists():
        raise ConflictError("Applications of this type are still open; decide them before changing its steps.",
                            code="has_open_applications")
    application_type.steps.all().delete()
    ApprovalStep.objects.bulk_create([
        ApprovalStep(organization_id=application_type.organization_id, application_type=application_type,
                     sequence=n, name=s["name"], permission=s["permission"])
        for n, s in enumerate(steps, 1)
    ])


def current_step(application: Application) -> ApprovalStep | None:
    return application.application_type.steps.filter(sequence=application.step).first()


# ---------------------------------------------------------------------------
# Who is who
# ---------------------------------------------------------------------------
def children_of(user):
    parent = parent_for_user(user)
    if parent is None:
        return []
    return [link.student for link in links_for_parent(parent)]


def is_party(user, application: Application) -> bool:
    """The person who sent it or the person it's about (or their parent)."""
    if user is None or not user.is_authenticated:
        return False
    if application.applicant_id == user.pk:
        return True
    if application.student_id and application.student.user_id == user.pk:
        return True
    if application.staff_id and application.staff.user_id == user.pk:
        return True
    return bool(application.student_id) and application.student_id in {s.pk for s in children_of(user)}


def can_decide(user, application: Application) -> bool:
    if application.status != Status.IN_REVIEW or is_party(user, application):
        return False
    step = current_step(application)
    return step is not None and holds(user, step.permission, application.campus_id)


def ensure_can_decide(user, application: Application) -> ApprovalStep:
    if application.status != Status.IN_REVIEW:
        raise ConflictError(f"This application is {application.get_status_display().lower()}.", code="not_in_review")
    if is_party(user, application):
        raise PermissionDeniedError("You can't decide your own application.", code="own_application")
    step = current_step(application)
    if step is None or not holds(user, step.permission, application.campus_id):
        raise PermissionDeniedError("This step is for someone else to decide.", code="not_your_step")
    return step


# ---------------------------------------------------------------------------
# Submitting
# ---------------------------------------------------------------------------
def _record(application, action, *, by=None, note="", step=None):
    step = step or (current_step(application) if application.status == Status.IN_REVIEW else None)
    return ApplicationEvent.objects.create(
        organization_id=application.organization_id, application=application, action=action,
        step=step.sequence if step else None, step_name=step.name if step else "", by=by, note=note)


def _tell_deciders(application: Application) -> None:
    step = current_step(application)
    if step is None:
        return
    recipients = [u for u in users_holding([step.permission], campus_id=application.campus_id,
                                           organization_id=application.organization_id)
                  if not is_party(u, application)]
    notify(recipients, event_type="applications.awaiting_decision",
           title=f"{application.application_type.name}: {application.subject_name}",
           body=f"Application {application.number} is waiting for you ({step.name}).",
           data={"application": application.pk}, organization_id=application.organization_id)


def _tell_applicant(application: Application, title: str, body: str = "") -> None:
    people = {application.applicant}
    if application.student_id and application.student.user_id:
        people.add(application.student.user)
    if application.staff_id and application.staff.user_id:
        people.add(application.staff.user)
    notify(people, event_type=f"applications.{application.status}", title=title, body=body,
           data={"application": application.pk}, organization_id=application.organization_id)


def submit(*, application_type, campus, raw_data, by=None, student=None, staff=None, contact=None,
           public: bool = False) -> tuple[Application, str | None]:
    """Open an application. Returns it and, for a public form, the secret
    token the applicant needs to check on it (shown once, stored hashed)."""
    if not application_type.is_active:
        raise ConflictError("This form isn't accepting applications.", code="type_inactive")
    if public and not application_type.is_public:
        raise ConflictError("This form needs you to sign in.", code="not_public")
    if application_type.campus_id and application_type.campus_id != campus.pk:
        raise ServiceError("This form is for another campus.", code="wrong_campus")
    if not application_type.steps.exists():
        raise ConflictError("This form has no approval steps yet.", code="no_steps")
    subject = KINDS[application_type.kind].subject
    if subject == "student" and student is None:
        raise ServiceError("Say which student this is for.", code="student_required")
    if subject == "staff" and staff is None:
        raise ServiceError("Say which staff member this is for.", code="staff_required")
    if subject == "none" and (student or staff):
        raise ServiceError("An admission application isn't about an existing student or staff member.",
                           code="no_subject_allowed")
    if student is not None and student.status != "active":
        raise ConflictError("Only an active student can apply.", code="not_active")
    if staff is not None and staff.status == "left":
        raise ConflictError("This staff member has left.", code="not_active")
    data = clean_data(application_type, raw_data, campus=campus, student=student, staff=staff)
    contact = contact or {}
    token = secrets.token_urlsafe(24) if public else None
    with transaction.atomic():
        application = Application.objects.create(
            organization_id=application_type.organization_id, application_type=application_type, campus=campus,
            number=_next_number(application_type.organization_id, "APP-", Application),
            applicant=by if by is not None and by.is_authenticated else None, student=student, staff=staff,
            contact_name=contact.get("name", ""), contact_email=contact.get("email", ""),
            contact_phone=contact.get("phone", ""), data=data, token_hash=_hash(token) if token else "",
            submitted_at=timezone.now())
        _record(application, EventAction.SUBMITTED, by=application.applicant)
        log(AuditLog.Action.CREATE, instance=application, module=MODULE, actor=application.applicant)
    _tell_deciders(application)
    return application, token


def find_public(organization, number: str, token: str) -> Application | None:
    """A public application by its number and token; ``None`` either way it
    fails, so a wrong token and a wrong number look the same."""
    application = Application.objects.filter(organization=organization, number=str(number).upper()).first()
    if application is None or not application.token_hash or not token:
        _hash("x" * 32)  # spend the same time either way
        return None
    return application if hmac.compare_digest(application.token_hash, _hash(token)) else None


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------
def _locked(application: Application) -> Application:
    return (Application.objects.select_for_update()
            .select_related("application_type", "student", "staff", "campus").get(pk=application.pk))


def approve(application: Application, *, by, note: str = "", decision: dict | None = None) -> Application:
    """Approve the current step. On the last one, the kind's handler acts."""
    with transaction.atomic():
        application = _locked(application)
        step = ensure_can_decide(by, application)
        last = not application.application_type.steps.filter(sequence__gt=step.sequence).exists()
        if not last:
            application.step = application.application_type.steps.filter(
                sequence__gt=step.sequence).order_by("sequence").first().sequence
            application.save(update_fields=["step", "updated_at"])
            _record(application, EventAction.APPROVED, by=by, note=note, step=step)
        else:
            cleaned = clean_decision(application, decision)
            outcome, label = KINDS[application.application_type.kind].fulfil(application, cleaned, by)
            application.status = Status.APPROVED
            application.decided_at = timezone.now()
            application.outcome, application.outcome_label = outcome, label
            application.save(update_fields=["status", "decided_at", "outcome", "outcome_label", "updated_at"])
            _record(application, EventAction.COMPLETED, by=by, note=note, step=step)
        log(AuditLog.Action.UPDATE, instance=application, module=MODULE, actor=by,
            changes={"step": {"before": step.sequence, "after": None if last else application.step}})
    if last:
        _tell_applicant(application, f"Approved: {application.application_type.name}", application.outcome_label)
    else:
        _tell_deciders(application)
    return application


def reject(application: Application, *, by, note: str) -> Application:
    if not note.strip():
        raise ServiceError("Say why it's rejected.", code="reason_required")
    with transaction.atomic():
        application = _locked(application)
        step = ensure_can_decide(by, application)
        application.status = Status.REJECTED
        application.decided_at = timezone.now()
        application.save(update_fields=["status", "decided_at", "updated_at"])
        _record(application, EventAction.REJECTED, by=by, note=note.strip(), step=step)
        log(AuditLog.Action.UPDATE, instance=application, module=MODULE, actor=by,
            changes={"status": {"before": Status.IN_REVIEW, "after": Status.REJECTED}})
    _tell_applicant(application, f"Rejected: {application.application_type.name}", note.strip())
    return application


def send_back(application: Application, *, by, note: str) -> Application:
    """Return it to the applicant to fix; it resumes at this same step."""
    if not note.strip():
        raise ServiceError("Say what needs changing.", code="reason_required")
    with transaction.atomic():
        application = _locked(application)
        step = ensure_can_decide(by, application)
        application.status = Status.RETURNED
        application.save(update_fields=["status", "updated_at"])
        _record(application, EventAction.RETURNED, by=by, note=note.strip(), step=step)
    _tell_applicant(application, f"Changes needed: {application.application_type.name}", note.strip())
    return application


def resubmit(application: Application, *, raw_data, by=None, note: str = "") -> Application:
    with transaction.atomic():
        application = _locked(application)
        if application.status != Status.RETURNED:
            raise ConflictError("Only an application sent back can be resubmitted.", code="not_returned")
        application.data = clean_data(application.application_type, raw_data, campus=application.campus,
                                      student=application.student, staff=application.staff)
        application.status = Status.IN_REVIEW
        application.save(update_fields=["data", "status", "updated_at"])
        _record(application, EventAction.RESUBMITTED, by=by, note=note)
    _tell_deciders(application)
    return application


def withdraw(application: Application, *, by=None, note: str = "") -> Application:
    with transaction.atomic():
        application = _locked(application)
        if application.status not in OPEN:
            raise ConflictError(f"This application is already {application.get_status_display().lower()}.",
                                code="not_open")
        application.status = Status.WITHDRAWN
        application.decided_at = timezone.now()
        application.save(update_fields=["status", "decided_at", "updated_at"])
        _record(application, EventAction.WITHDRAWN, by=by, note=note)
    return application


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------
def certificate_contents(student) -> dict:
    """What a certificate states, frozen at issue."""
    enrollment = get_current_enrollment(student)
    section = getattr(enrollment, "section", None)
    return {
        "student_name": student.full_name, "student_number": student.student_number,
        "date_of_birth": student.date_of_birth.isoformat() if student.date_of_birth else None,
        "gender": student.gender, "status": student.status,
        "admitted_on": student.admitted_on.isoformat() if student.admitted_on else None,
        "campus": student.campus.name, "organization": student.organization.name,
        "program": section.program.name if section else None,
        "level": section.level if section else None,
        "section": section.name if section else None,
        "academic_year": section.academic_year.name if section else None,
    }


def issue_certificate(*, student, title: str, purpose: str = "", application=None, by=None) -> Certificate:
    if by is not None and not holds(by, CERTIFY, student.campus_id):
        raise PermissionDeniedError("Your role does not cover this campus for this action.", code="wrong_campus")
    with transaction.atomic():
        certificate = Certificate.objects.create(
            organization_id=student.organization_id, student=student, application=application, title=title,
            number=_next_number(student.organization_id, "CERT-", Certificate), purpose=purpose,
            issued_on=timezone.localdate(), contents=certificate_contents(student), issued_by=by)
        log(AuditLog.Action.CREATE, instance=certificate, module=MODULE, actor=by)
    return certificate


def revoke_certificate(certificate: Certificate, *, reason: str, by=None) -> Certificate:
    if not reason.strip():
        raise ServiceError("Say why it's revoked.", code="reason_required")
    with transaction.atomic():
        certificate = Certificate.objects.select_for_update().get(pk=certificate.pk)
        if certificate.revoked_at is not None:
            raise ConflictError("Already revoked.", code="already_revoked")
        certificate.revoked_at, certificate.revoked_reason, certificate.revoked_by = timezone.now(), reason.strip(), by
        certificate.save(update_fields=["revoked_at", "revoked_reason", "revoked_by", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=certificate, module=MODULE, actor=by,
            changes={"revoked_reason": {"before": "", "after": reason.strip()}})
    return certificate
