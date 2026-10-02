"""Cross-tenant sweep over every route of /api/v1/.

``test_tenant_isolation`` checks a few endpoints by hand. This walks the URL
router instead, so an endpoint added later is attacked automatically — or,
when the sweep can't attack it, a guard test fails until it's taught how.

Two organizations get one row of every tenant model: ``alpha`` (the attacker's)
and ``zqtenantb`` (the victim's). Every victim row carries that marker in its
names, codes or e-mails, so a leak shows up as the marker in a response body.
The attacker holds every permission organization-wide: only the tenant
boundary stands between them and org B.

Each attack runs inside a savepoint that is rolled back, and checks that

* the response doesn't contain the marker,
* no row of org B changed, and
* no row of one organization points at a row of another.

The last check catches a foreign id accepted in a write (e.g. an alpha
student placed into a zqtenantb section) however the view is written.
"""
import re
from dataclasses import dataclass
from datetime import date, timedelta

from django.apps import apps
from django.db import transaction
from django.db.models import F
from django.urls import URLResolver, get_resolver
from django.utils import timezone
from rest_framework import serializers
from rest_framework.relations import ManyRelatedField, RelatedField
from rest_framework.test import APIRequestFactory, force_authenticate

from core.audit.models import AuditLog
from modules.attendance import qr as attendance_qr
from modules.attendance.models import AttendanceCorrection, Punch, StaffAttendanceDay, StaffWorkSchedule
from modules.examinations import services as exam_services
from modules.events import services as events_services
from modules.events.models import (
    Award,
    AwardRule,
    Event,
    EventAttendance,
    EventCategory,
    EventParticipation,
    EventRegistration,
    PointEntry,
    PointRule,
    StudentAward,
    StudentPoints,
)
from modules.communication.models import Appointment, AppointmentSlot, Message, MessageThread
from modules.inventory.models import (
    Asset,
    AssetAssignment,
    Disposal,
    Item,
    ItemCategory,
    MaintenanceRecord,
    PurchaseLine,
    PurchaseOrder,
    StockIssue,
    StockIssueLine,
    StockLevel,
    StockMovement,
    StockTransfer,
    Store,
    Supplier,
)
from modules.hr.models import (
    Contract,
    EmployeeProfile,
    FiscalYear,
    LeaveBalance,
    LeaveRequest,
    LeaveType,
    Position,
    StaffDocument,
)
from modules.payroll.models import (
    PayComponent,
    PayrollAdjustment,
    PayrollRun,
    PayrollSettings,
    Payslip,
    SalaryStructure,
    SalaryStructureLine,
    StaffSalary,
    StaffSalaryLine,
    TaxScheme,
    TaxSlab,
)
from modules.applications.models import Application, ApplicationEvent, ApplicationType, ApprovalStep, Certificate
from core.files.models import StoredFile
from core.api_keys.models import ApiKey
from core.signup.models import SignupRequest
from core.accounts.models import User as AccountUser
from modules.alumni.models import (
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
)
from modules.careers.models import Candidacy, Interview, JobOffer, JobPosting, Vacancy
from modules.hostel.models import Allocation as HostelAllocation
from modules.hostel.models import Bed, Building, Complaint, Floor, HostelRoom, RoomType
from modules.transport.models import Assignment as RideAssignment
from modules.transport.models import Driver, FuelLog, Route as BusRoute, Stop, Trip, TripRecord, Vehicle
from modules.transport.models import Maintenance as VehicleMaintenance
from modules.transport.models import VehicleDocument
from modules.library.models import Author, Book, Category, Copy, Fine, Issue, Member, Publisher, Reservation, Shelf
from modules.finance import services as finance_services
from modules.finance.models import (
    FeeCategory,
    FeeStructure,
    FeeStructureItem,
    Installment,
    Invoice,
    InvoiceItem,
    Payment,
    Receipt,
    Refund,
    Scholarship,
    StudentScholarship,
)
from modules.examinations.models import (
    AdmitCard,
    Exam,
    ExamComponent,
    ExamRoom,
    ExamSubject,
    ExamType,
    GradeScale,
    Invigilation,
    Mark,
    MarkCorrection,
    MarkSheet,
    Result,
    ResultPlan,
    ResultPlanExam,
    SeatAllocation,
    SubjectResult,
)
from core.organizations.models import Organization
from core.permissions.models import Permission
from modules.notices.models import Notice
from modules.notifications.models import Notification
from modules.parents.services import link_student
from modules.students.models import Enrollment
from modules.students.services import place_student
from modules.support.models import SupportTicket, TicketComment
from tests.base import APITestCaseBase
from tests.factories import (
    add_to_curriculum,
    create_academic_year,
    create_admission,
    create_attendance_record,
    create_attendance_session,
    create_batch,
    create_bell_schedule,
    create_calendar_event,
    create_campus,
    create_department,
    create_device,
    create_lesson_change,
    create_organization,
    create_parent,
    create_period,
    create_program,
    create_role,
    create_room,
    create_section,
    create_staff_member,
    create_student,
    create_student_elective,
    create_subject,
    create_teaching_assignment,
    create_term,
    create_timetable_entry,
    create_user,
    grant,
    map_pin,
    create_work_schedule,
    user_with_permissions,
)

MARK = "zqtenantb"
ATTACKER_TAG = "alpha"

# Not tenant data: the same rows for everyone.
GLOBAL_MODELS = {Permission}

# Views outside the viewset router that only ever act on the caller's own
# account, so there is no other tenant's id to send them.
SELF_ONLY_VIEWS = {
    "LoginView", "RefreshView", "LogoutView", "CurrentUserView", "ChangePasswordView",
    "TwoFactorStatusView", "TwoFactorSetupView", "TwoFactorConfirmView",
    "TwoFactorRecoveryCodesView", "TwoFactorDisableView", "APIRootView",
    # Signed in as a device, which only ever writes into its own organization;
    # tested in modules/attendance/tests/test_devices.py.
    "DevicePunchView",
    # Authenticated normally, but every foreign id it could act on (a campus) is checked against
    # the caller's own organization in the serializer before any query runs — see the tenant test
    # in modules/finance/tests/test_access.py.
    "AssessLateFeesView",
    # Public application forms: no account; the organization is named by its code in the
    # URL and every id in the body (form, campus) is looked up inside it — tenant tests in
    # modules/applications/tests (PublicAdmissionTests.test_refusals).
    "PublicTypesView", "PublicSubmitView", "PublicStatusView", "PublicResubmitView", "PublicWithdrawView",
    # Public careers pages, the same way: the organization comes from the URL, the vacancy is
    # looked up inside it and an offer only through that organization's number and token —
    # modules/careers/tests (ApplyTests.test_public_refusals, PublicTenantTests).
    "PublicVacanciesView", "PublicVacancyView", "PublicApplyView", "PublicOfferView", "PublicOfferRespondView",
    # Public signup and password reset (core/signup): no account and no record id in the body.
    # Signup makes a new organization; the rest name only a code, an email, or a single-use
    # token — tests in core/signup/tests.
    "SignupConfigView", "StartSignupView", "ResendView", "VerifyView", "CheckCodeView",
    "PasswordResetRequestView", "PasswordResetConfirmView",
}

CRUD_ACTIONS = {"list", "create", "retrieve", "update", "partial_update", "destroy"}

# Query parameters that name a record. Every collection GET is sent each one
# pointing at org B's record; filtering by a foreign id must not reach it.
QUERY_PARAMS = {
    "organization": "organization", "campus": "campus", "section": "section",
    "student": "student", "parent": "parent", "teacher": "staff", "staff": "staff",
    "program": "program", "academic_year": "academic_year", "subject": "subject",
    "room": "room", "schedule": "schedule", "term": "term", "batch": "batch",
    "department": "department", "user": "user", "role": "role",
    "session": "attendance_session", "device": "device", "timetable_entry": "timetable_entry",
    "exam": "exam", "plan": "result_plan", "exam_subject": "exam_subject", "exam_room": "exam_room",
    "sheet": "mark_sheet", "component": "exam_component", "grade_scale": "grade_scale",
    "exam_type": "exam_type", "enrollment": "enrollment", "level": "exam",
    "invoice": "invoice", "payment": "payment", "fee_structure": "fee_structure", "scholarship": "scholarship",
    "category": "event_category", "organized_by": "staff", "event": "event", "rule": "point_rule",
    "award": "award",
    "slot": "appointment_slot", "assigned_to": "user",
    "book": "library_book", "shelf": "library_shelf", "member": "library_member",
    "publisher": "library_publisher",
    "item": "inventory_item", "store": "inventory_store", "from_store": "inventory_store",
    "to_store": "inventory_store", "supplier": "inventory_supplier", "asset": "inventory_asset",
    "purchase_line": "inventory_po_line", "stock_issue": "inventory_stock_issue",
    "transfer": "inventory_transfer",
    "position": "hr_position", "leave_type": "hr_leave_type", "fiscal_year": "hr_fiscal_year",
    "structure": "payroll_structure", "run": "payroll_run", "payslip": "payroll_payslip",
    "corrects": "payroll_payslip",
    "building": "hostel_building", "floor": "hostel_floor", "room_type": "hostel_room_type",
    "bed": "hostel_bed", "bed__room": "hostel_room", "bed__room__building": "hostel_building",
    "room__building": "hostel_building",
    "vehicle": "transport_vehicle", "driver": "transport_driver", "route": "transport_route",
    "stop": "transport_stop", "trip": "transport_trip", "assignment": "transport_assignment",
    "assignment__student": "student", "assignment__staff": "staff", "trip__route": "transport_route",
    "application_type": "application_type", "application": "application",
    "profile": "alumni_profile", "mentor": "alumni_profile", "donor": "alumni_profile",
    "campaign": "alumni_campaign", "vacancy": "careers_vacancy", "candidacy": "careers_candidacy",
}


def _later():
    return date.today() + timedelta(days=30)


def next_monday():
    today = date.today()
    return today + timedelta(days=7 - today.weekday())


# ---------------------------------------------------------------------------
# One of everything, per organization
# ---------------------------------------------------------------------------
def build_tenant(tag):
    """One row of every tenant model, named after ``tag``."""
    org = create_organization(code=f"{tag}-org", name=f"{tag} Org")
    campus = create_campus(org, code=f"{tag}-main", name=f"{tag} Main")
    user = create_user(org, email=f"user@{tag}.test", first_name=tag)
    role = create_role(org, code=f"{tag}-role", name=f"{tag} Role")
    grant(user, role, campus=campus)
    staff = create_staff_member(campus, employee_number=f"{tag}-e1", first_name=tag)

    department = create_department(org, code=f"{tag}-dept", name=f"{tag} Dept", head=staff)
    program = create_program(org, code=f"{tag}-prog", name=f"{tag} Program", department=department)
    subject = create_subject(org, code=f"{tag}-phys", name=f"{tag} Physics")
    elective = create_subject(org, code=f"{tag}-comp", name=f"{tag} Computer")
    curriculum = add_to_curriculum(program, subject, 11)
    add_to_curriculum(program, elective, 11, is_elective=True)
    year = create_academic_year(org, name=f"{tag} 2082/83")
    term = create_term(year, name=f"{tag} Term 1")
    room = create_room(campus, code=f"{tag}-r101", name=f"{tag} Room")
    batch = create_batch(campus, program, year, code=f"{tag}-batch", name=f"{tag} Batch")
    section = create_section(campus, program, year, name=f"{tag}-A", batch=batch, home_room=room)
    assignment = create_teaching_assignment(section, subject, staff)

    student = create_student(campus, student_number=f"{tag}-s1", first_name=tag)
    place_student(student=student, section=section)
    enrollment = Enrollment.objects.get(student=student, section=section)
    student_elective = create_student_elective(enrollment, elective)
    parent = create_parent(org, first_name=tag)
    link_student(parent=parent, student=student, relationship="father")
    admission = create_admission(campus, application_number=f"{tag}-a1", first_name=tag)

    event = create_calendar_event(org, title=f"{tag} Sports day")
    schedule = create_bell_schedule(campus, name=f"{tag} Day shift")
    period = create_period(schedule, f"{tag} P1", "08:00", "08:45")
    entry = create_timetable_entry(assignment, period, 1, room=room)
    change = create_lesson_change(entry, next_monday(), note=f"{tag} cancelled")
    audit = AuditLog.objects.create(
        organization=org, action=AuditLog.Action.UPDATE, module="sweep",
        object_repr=f"{tag} audited",
    )

    roll_call = create_attendance_session(section)
    lesson_session = create_attendance_session(section, entry=entry)
    record = create_attendance_record(roll_call, enrollment)
    correction = AttendanceCorrection.objects.create(
        organization=org, record=record, old_status="absent", new_status="present",
        reason=f"{tag} correction",
    )
    work_schedule = create_work_schedule(campus, name=f"{tag} Office hours", is_default=True)
    staff_schedule = StaffWorkSchedule.objects.create(organization=org, staff=staff,
                                                      schedule=work_schedule)
    device = create_device(campus, serial_number=f"{tag}-sn", name=f"{tag} Gate")
    identity = map_pin(f"{tag}-1", staff=staff)
    punch = Punch.objects.create(
        organization=org, campus=campus, device=device, pin=identity.pin, staff=staff,
        punched_at=timezone.now(), source="biometric", dedupe_key=f"{tag}-punch",
    )
    staff_day = StaffAttendanceDay.objects.create(organization=org, staff=staff,
                                                  date=date.today(), status="present")

    # Phase 5 — examinations
    scale = GradeScale.objects.create(organization=org, name=f"{tag} Scale")
    bands, divisions = exam_services.preset_bands("neb-style")
    exam_services.replace_bands(scale, bands, divisions)
    exam_type = ExamType.objects.create(organization=org, code=f"{tag}-term", name=f"{tag} Terminal")
    exam = Exam.objects.create(organization=org, campus=campus, academic_year=year, exam_type=exam_type,
                               program=program, name=f"{tag} Terminal", grade_scale=scale,
                               status=Exam.Status.SCHEDULED, start_date=date.today() - timedelta(days=2),
                               end_date=date.today() - timedelta(days=2))
    paper = ExamSubject.objects.create(organization=org, exam=exam, subject=subject, level=11,
                                       date=date.today() - timedelta(days=2), start_time="10:00", end_time="12:00")
    component = ExamComponent.objects.create(organization=org, exam_subject=paper, name=f"{tag} Theory",
                                             full_marks=100, pass_marks=35)
    exam_room = ExamRoom.objects.create(organization=org, exam=exam, room=room, capacity=10)
    seat = SeatAllocation.objects.create(organization=org, exam=exam, enrollment=enrollment,
                                         exam_room=exam_room, seat_number=1)
    invigilation = Invigilation.objects.create(organization=org, exam_subject=paper, exam_room=exam_room, staff=staff)
    admit_card = AdmitCard.objects.create(organization=org, exam=exam, enrollment=enrollment, student=student,
                                          card_number=f"{tag}-card")
    sheet = MarkSheet.objects.create(organization=org, exam_subject=paper, section=section)
    mark = Mark.objects.create(organization=org, sheet=sheet, component=component, enrollment=enrollment,
                               status="present", marks=50)
    mark_correction = MarkCorrection.objects.create(
        organization=org, mark=mark, old_status="present", old_marks=40, new_status="present", new_marks=50,
        reason=f"{tag} recount")
    result = Result.objects.create(organization=org, exam=exam, student=student, enrollment=enrollment,
                                   section=section, total_obtained=50, total_full=100, percentage=50,
                                   status="pass")
    SubjectResult.objects.create(result=result, subject=subject, exam_subject=paper, obtained=50, full=100,
                                 percentage=50, status="pass")
    plan = ResultPlan.objects.create(organization=org, campus=campus, academic_year=year, program=program,
                                     name=f"{tag} Term Plan", grade_scale=scale)
    ResultPlanExam.objects.create(plan=plan, exam=exam, weight=100)

    # Phase 6 — finance
    fee_category = FeeCategory.objects.create(organization=org, code=f"{tag}-fee", name=f"{tag} Tuition")
    fee_structure = FeeStructure.objects.create(organization=org, program=program, level=11, academic_year=year,
                                                name=f"{tag} Structure")
    fee_structure_item = FeeStructureItem.objects.create(organization=org, fee_structure=fee_structure,
                                                         category=fee_category, amount=1000, frequency="per_term")
    scholarship = Scholarship.objects.create(organization=org, name=f"{tag} Scholarship", kind="flat", value=100)
    student_scholarship = StudentScholarship.objects.create(organization=org, student=student, scholarship=scholarship,
                                                             started_on=year.start_date)
    invoice = Invoice.objects.create(organization=org, campus=campus, student=student, enrollment=enrollment,
                                     fee_structure=fee_structure, academic_year=year, term=term,
                                     invoice_number=f"{tag}-INV-1", issue_date=date.today(),
                                     due_date=date.today() + timedelta(days=15), total=1000)
    invoice_item = InvoiceItem.objects.create(organization=org, invoice=invoice, category=fee_category, kind="fee",
                                              description=f"{tag} Tuition", amount=1000)
    installment = Installment.objects.create(organization=org, invoice=invoice, sequence=1, amount=1000,
                                             due_date=date.today() + timedelta(days=15))
    payment = Payment.objects.create(organization=org, campus=campus, invoice=invoice, amount=500, method="cash",
                                     paid_at=timezone.now())
    receipt = Receipt.objects.create(organization=org, payment=payment, receipt_number=f"{tag}-RC-1",
                                     issued_at=timezone.now())
    refund = Refund.objects.create(organization=org, payment=payment, amount=100, reason=f"{tag} refund",
                                   refunded_at=timezone.now())

    # Phase 7 — events
    event_category = EventCategory.objects.create(organization=org, code=f"{tag}-cat", name=f"{tag} Category")
    my_event = Event.objects.create(organization=org, campus=campus, category=event_category, name=f"{tag} Event",
                                    start_at=timezone.now() + timedelta(days=7),
                                    end_at=timezone.now() + timedelta(days=7, hours=2), status="published",
                                    registration_mode="open", organized_by=staff)
    event_registration = EventRegistration.objects.create(organization=org, event=my_event, student=student,
                                                          status="confirmed")
    event_attendance = EventAttendance.objects.create(organization=org, event=my_event, student=student,
                                                      status="present")
    event_participation = EventParticipation.objects.create(organization=org, event=my_event, student=student,
                                                             role="winner")
    point_rule = PointRule.objects.create(organization=org, name=f"{tag} rule", category=event_category,
                                          source="attendance", points=10)
    point_entry = PointEntry.objects.create(organization=org, student=student, points=10, reason=f"{tag} reason",
                                            rule=point_rule, event=my_event)
    student_points = StudentPoints.objects.create(organization=org, student=student, total=10)
    award = Award.objects.create(organization=org, kind="badge", code=f"{tag}-award", name=f"{tag} Award")
    award_rule = AwardRule.objects.create(organization=org, award=award, threshold_kind="points_total",
                                          threshold_value=10, category=event_category)
    student_award = StudentAward.objects.create(organization=org, student=student, award=award,
                                                awarded_at=timezone.now())

    # Phase 8 — communication
    notification = Notification.objects.create(organization=org, recipient=user, event_type="test.event",
                                               title=f"{tag} notice")
    notice = Notice.objects.create(organization=org, campus=campus, audience="all", title=f"{tag} Notice",
                                   body=f"{tag} body", published_at=timezone.now())
    other_comm_user = create_user(org, email=f"comm-other@{tag}.test", first_name=f"{tag}Other",
                                  user_type="student")
    message_thread = MessageThread.objects.create(organization=org, campus=campus, staff_user=user,
                                                  other_user=other_comm_user, subject=f"{tag} thread")
    message = Message.objects.create(organization=org, thread=message_thread, sender=user, body=f"{tag} message")
    appointment_slot = AppointmentSlot.objects.create(
        organization=org, campus=campus, staff=staff, starts_at=timezone.now() + timedelta(days=1),
        ends_at=timezone.now() + timedelta(days=1, minutes=30), location=f"{tag} Room")
    appointment = Appointment.objects.create(organization=org, slot=appointment_slot, requested_by=user,
                                             student=student, reason=f"{tag} reason")
    support_ticket = SupportTicket.objects.create(organization=org, campus=campus, raised_by=user,
                                                  subject=f"{tag} ticket", description=f"{tag} description")
    ticket_comment = TicketComment.objects.create(organization=org, ticket=support_ticket, author=user,
                                                  body=f"{tag} comment")

    # Phase 9 — library
    author = Author.objects.create(organization=org, name=f"{tag} Author")
    category = Category.objects.create(organization=org, code=f"{tag}-cat", name=f"{tag} Category")
    publisher = Publisher.objects.create(organization=org, name=f"{tag} Publisher")
    book = Book.objects.create(organization=org, title=f"{tag} Book", category=category, publisher=publisher)
    book.authors.add(author)
    shelf = Shelf.objects.create(organization=org, campus=campus, code=f"{tag}-A1")
    copy = Copy.objects.create(organization=org, book=book, campus=campus, shelf=shelf,
                               accession_number=f"{tag}-ACC-1", price="500.00")
    member = Member.objects.create(organization=org, campus=campus, student=student, membership_type="student",
                                   member_number=f"{tag}-LM-1", max_books=3, loan_period_days=14,
                                   daily_fine_rate="5.00", joined_on=timezone.localdate())
    issue = Issue.objects.create(organization=org, copy=copy, member=member, issued_at=timezone.now(),
                                 due_at=timezone.now() + timedelta(days=14))
    copy.status = "issued"
    copy.save(update_fields=["status"])
    fine = Fine.objects.create(organization=org, member=member, issue=issue, category="overdue", amount="10.00")
    reservation = Reservation.objects.create(organization=org, book=book, member=member)

    # Phase 10 — inventory
    item_category = ItemCategory.objects.create(organization=org, code=f"{tag}-icat", name=f"{tag} Item Category")
    supplier = Supplier.objects.create(organization=org, name=f"{tag} Supplier")
    stock_item = Item.objects.create(organization=org, category=item_category, code=f"{tag}-item",
                                     name=f"{tag} Item", reorder_level=2)
    asset_item = Item.objects.create(organization=org, code=f"{tag}-aitem", name=f"{tag} Laptop", kind="asset")
    store = Store.objects.create(organization=org, campus=campus, code=f"{tag}-st", name=f"{tag} Store")
    store2 = Store.objects.create(organization=org, campus=campus, code=f"{tag}-st2", name=f"{tag} Store 2")
    stock_level = StockLevel.objects.create(organization=org, item=stock_item, store=store, quantity=10)
    stock_movement = StockMovement.objects.create(organization=org, item=stock_item, store=store, kind="receipt",
                                                  delta=10, balance_after=10, note=f"{tag} movement")
    stock_transfer = StockTransfer.objects.create(organization=org, item=stock_item, from_store=store,
                                                  to_store=store2, quantity=1)
    stock_issue = StockIssue.objects.create(organization=org, number=f"{tag}-SI-1", store=store, staff=staff,
                                            issued_on=timezone.localdate())
    stock_issue_line = StockIssueLine.objects.create(organization=org, issue=stock_issue, item=stock_item,
                                                     quantity=1)
    purchase_order = PurchaseOrder.objects.create(organization=org, number=f"{tag}-PO-1", supplier=supplier,
                                                  store=store, campus=campus, status="ordered")
    purchase_line = PurchaseLine.objects.create(organization=org, order=purchase_order, item=stock_item,
                                                quantity=5, unit_price="10.00")
    asset = Asset.objects.create(organization=org, item=asset_item, store=store, campus=campus,
                                 tag=f"{tag}-AST-1", serial_number=f"{tag}-SN", status="assigned")
    asset_assignment = AssetAssignment.objects.create(organization=org, asset=asset, staff=staff,
                                                      assigned_on=timezone.localdate())
    maintenance = MaintenanceRecord.objects.create(organization=org, asset=asset, kind="repair",
                                                   description=f"{tag} repair")
    old_asset = Asset.objects.create(organization=org, item=asset_item, store=store, campus=campus,
                                     tag=f"{tag}-AST-2", status="disposed")
    disposal = Disposal.objects.create(organization=org, asset=old_asset, method="scrapped",
                                       disposed_on=timezone.localdate(), reason=f"{tag} disposal")

    # Phase 11 — HR and payroll
    position = Position.objects.create(organization=org, code=f"{tag}-lect", name=f"{tag} Lecturer")
    contract = Contract.objects.create(organization=org, staff=staff, kind="permanent", position=position,
                                       department=department, start_date=date(2020, 1, 1),
                                       reference=f"{tag}-appointment")
    profile = EmployeeProfile.objects.create(organization=org, staff=staff, pan_number=f"{tag}-pan",
                                             bank_account_name=f"{tag} account")
    document = StaffDocument.objects.create(organization=org, staff=staff, kind="cv", title=f"{tag} CV")
    fiscal_year = FiscalYear.objects.create(organization=org, name=f"{tag} FY",
                                            start_date=date.today() - timedelta(days=100),
                                            end_date=date.today() + timedelta(days=200))
    leave_type = LeaveType.objects.create(organization=org, code=f"{tag}-casual", name=f"{tag} Casual",
                                          annual_quota=12)
    leave_balance = LeaveBalance.objects.create(organization=org, staff=staff, leave_type=leave_type,
                                                fiscal_year=fiscal_year, entitled=12)
    leave_request = LeaveRequest.objects.create(
        organization=org, staff=staff, leave_type=leave_type, fiscal_year=fiscal_year,
        start_date=date.today() + timedelta(days=7), end_date=date.today() + timedelta(days=7), days=1,
        reason=f"{tag} leave")
    payroll_settings = PayrollSettings.objects.create(organization=org)
    pay_component = PayComponent.objects.create(organization=org, code=f"{tag}-da", name=f"{tag} Allowance",
                                                kind="earning")
    salary_structure = SalaryStructure.objects.create(organization=org, code=f"{tag}-grade", name=f"{tag} Grade",
                                                      basic=10000)
    SalaryStructureLine.objects.create(structure=salary_structure, component=pay_component, value=100)
    old_salary = StaffSalary.objects.create(organization=org, staff=staff, structure=salary_structure,
                                            effective_from=date(2020, 1, 1), effective_to=date(2020, 12, 31),
                                            note=f"{tag} old salary")
    staff_salary = StaffSalary.objects.create(organization=org, staff=staff, structure=salary_structure,
                                              effective_from=date(2021, 1, 1), note=f"{tag} salary")
    StaffSalaryLine.objects.create(salary=staff_salary, component=pay_component, value=200)
    tax_scheme = TaxScheme.objects.create(organization=org, fiscal_year=fiscal_year, tax_status="single",
                                          name=f"{tag} Tax")
    TaxSlab.objects.create(scheme=tax_scheme, sequence=1, upto=None, rate=1)
    payroll_run = PayrollRun.objects.create(organization=org, campus=campus, name=f"{tag} Run",
                                            period_start=date(2020, 1, 1), period_end=date(2020, 1, 30),
                                            computed_at=timezone.now())
    payslip = Payslip.objects.create(organization=org, run=payroll_run, staff=staff, salary=old_salary,
                                     number=f"{tag}-PS-1", basic=10000, basis_days=26, working_days=26,
                                     tax_scheme=tax_scheme)
    payroll_adjustment = PayrollAdjustment.objects.create(organization=org, staff=staff, kind="earning",
                                                          amount=100, description=f"{tag} bonus")

    # Phase 12 — hostel and transport
    building = Building.objects.create(organization=org, campus=campus, code=f"{tag}-hall", name=f"{tag} Hall",
                                       warden=staff)
    floor = Floor.objects.create(organization=org, building=building, number=0, name=f"{tag} Ground")
    room_type = RoomType.objects.create(organization=org, code=f"{tag}-double", name=f"{tag} Double",
                                        fee_per_term=1000, fee_category=fee_category)
    hostel_room = HostelRoom.objects.create(organization=org, building=building, floor=floor, number=f"{tag}-G01",
                                            room_type=room_type)
    bed = Bed.objects.create(organization=org, room=hostel_room, label="A")
    bed2 = Bed.objects.create(organization=org, room=hostel_room, label="B")
    hostel_allocation = HostelAllocation.objects.create(organization=org, bed=bed, student=student,
                                                        start_date=date.today(), note=f"{tag} stay")
    complaint = Complaint.objects.create(organization=org, building=building, room=hostel_room, raised_by=user,
                                         title=f"{tag} fan broken")
    vehicle = Vehicle.objects.create(organization=org, campus=campus, registration_number=f"{tag}-BA-1",
                                     name=f"{tag} Bus", capacity=30)
    vehicle_document = VehicleDocument.objects.create(organization=org, vehicle=vehicle, kind="insurance",
                                                      number=f"{tag}-ins")
    driver = Driver.objects.create(organization=org, staff=staff, license_number=f"{tag}-lic")
    bus_route = BusRoute.objects.create(organization=org, campus=campus, code=f"{tag}-r1", name=f"{tag} Route",
                                        vehicle=vehicle, driver=driver, fee_per_term=500, fee_category=fee_category)
    stop = Stop.objects.create(organization=org, route=bus_route, sequence=1, name=f"{tag} Stop")
    ride = RideAssignment.objects.create(organization=org, route=bus_route, stop=stop, student=student,
                                         start_date=date.today())
    trip = Trip.objects.create(organization=org, route=bus_route, date=date.today(), direction="pickup",
                               vehicle=vehicle, driver=driver)
    trip_record = TripRecord.objects.create(organization=org, trip=trip, assignment=ride, status="boarded")
    vehicle_maintenance = VehicleMaintenance.objects.create(organization=org, vehicle=vehicle, kind="service",
                                                            date=date.today(), description=f"{tag} service")
    fuel_log = FuelLog.objects.create(organization=org, vehicle=vehicle, date=date.today(), litres=10, cost=1000,
                                      note=f"{tag} fuel")

    # Phase 13 — applications
    application_type = ApplicationType.objects.create(organization=org, code=f"{tag}-hostel",
                                                      name=f"{tag} Hostel form", kind="hostel")
    ApprovalStep.objects.create(organization=org, application_type=application_type, sequence=1,
                                name=f"{tag} Warden", permission="hostel.manage")
    application = Application.objects.create(organization=org, application_type=application_type, campus=campus,
                                             number=f"{tag}-APP-1", applicant=user, student=student,
                                             data={"note": f"{tag} please"}, submitted_at=timezone.now())
    ApplicationEvent.objects.create(organization=org, application=application, action="submitted",
                                    note=f"{tag} sent")
    certificate = Certificate.objects.create(organization=org, student=student, title=f"{tag} Character",
                                             number=f"{tag}-CERT-1", issued_on=date.today(),
                                             contents={"student_name": tag})

    # Phase 14 — alumni and careers (and the shared file store)
    alumni_profile = AlumniProfile.objects.create(organization=org, campus=campus, first_name=f"{tag} Alumna",
                                                  last_name="Graduate", program=program, academic_year="2080/81",
                                                  is_mentor=True, directory_visible=True)
    employment = Employment.objects.create(organization=org, profile=alumni_profile, employer=f"{tag} Corp",
                                           title=f"{tag} Engineer", start_date=date(2023, 1, 1))
    higher_study = HigherStudy.objects.create(organization=org, profile=alumni_profile,
                                              institution=f"{tag} University", qualification="MSc", start_year=2024)
    achievement = Achievement.objects.create(organization=org, profile=alumni_profile, title=f"{tag} Prize")
    alumni_event = AlumniEvent.objects.create(organization=org, campus=campus, title=f"{tag} Reunion",
                                              starts_at=timezone.now() + timedelta(days=30), status="published")
    rsvp = Rsvp.objects.create(organization=org, event=alumni_event, profile=alumni_profile, response="going")
    mentorship = Mentorship.objects.create(organization=org, mentor=alumni_profile, student=student,
                                           topic=f"{tag} career advice")
    campaign = Campaign.objects.create(organization=org, campus=campus, code=f"{tag}-library",
                                       name=f"{tag} Library fund", starts_on=date.today())
    donation = Donation.objects.create(organization=org, campus=campus, campaign=campaign, donor=alumni_profile,
                                       donor_name=f"{tag} Donor", amount=1000, received_on=date.today(),
                                       receipt_number=f"{tag}-DON-1")
    donation_refund = DonationRefund.objects.create(organization=org, donation=donation, amount=100,
                                                    refunded_on=date.today(), reason=f"{tag} refund")
    job_form = ApplicationType.objects.create(organization=org, code=f"{tag}-job", name=f"{tag} Job form",
                                              kind="job", is_public=True)
    ApprovalStep.objects.create(organization=org, application_type=job_form, sequence=1, name=f"{tag} Principal",
                                permission="careers.hire")
    vacancy = Vacancy.objects.create(organization=org, campus=campus, application_type=job_form,
                                     code=f"{tag}-teacher", title=f"{tag} Teacher", status="open",
                                     opens_on=date.today())
    job_application = Application.objects.create(organization=org, application_type=job_form, campus=campus,
                                                 number=f"{tag}-APP-2", data={"first_name": tag},
                                                 submitted_at=timezone.now())
    candidacy = Candidacy.objects.create(organization=org, vacancy=vacancy, application=job_application,
                                         full_name=f"{tag} Candidate", email=f"candidate@{tag}.test")
    stored_file = StoredFile.objects.create(organization=org, file=f"{org.pk}/{tag}.pdf", name=f"{tag} cv.pdf",
                                            content_type="application/pdf", extension="pdf", size=10,
                                            sha256="0" * 64, purpose="resume", uploaded_by=user,
                                            owner_type="careers.candidacy", owner_id=candidacy.pk)
    candidacy.resume = stored_file
    candidacy.save(update_fields=["resume"])
    interview = Interview.objects.create(organization=org, candidacy=candidacy, location=f"{tag} Room",
                                         scheduled_at=timezone.now() + timedelta(days=3))
    interview.panel.set([staff])
    job_offer = JobOffer.objects.create(organization=org, candidacy=candidacy, start_date=_later(),
                                        contract_kind="probation", salary_note=f"{tag} grade")
    job_posting = JobPosting.objects.create(organization=org, title=f"{tag} Developer", company=f"{tag} Ltd",
                                            description="x", apply_url="https://example.com", status="approved")

    # SaaS — API keys, each with its own integration user
    key_user = AccountUser.objects.create_user(email=f"key@{tag}.test", password=None, organization=org,
                                               user_type="integration", first_name=f"{tag} Key")
    api_key = ApiKey.objects.create(organization=org, user=key_user, name=f"{tag} Website", prefix=f"{tag}key"[:16],
                                    secret_hash="0" * 64)
    # The signup the organization was created from.
    signup = SignupRequest.objects.create(organization_name=org.name, organization_code=org.code,
                                          admin_email=f"founder@{tag}.test", admin_first_name=f"{tag} Founder",
                                          token_expires_at=timezone.now(), status="completed", organization=org)

    return {
        "api_key": api_key, "signup_request": signup,
        "alumni_profile": alumni_profile, "alumni_employment": employment, "alumni_study": higher_study,
        "alumni_achievement": achievement, "alumni_event": alumni_event, "alumni_rsvp": rsvp,
        "alumni_mentorship": mentorship, "alumni_campaign": campaign, "alumni_donation": donation,
        "alumni_refund": donation_refund, "careers_vacancy": vacancy, "careers_candidacy": candidacy,
        "careers_interview": interview, "careers_offer": job_offer, "careers_posting": job_posting,
        "stored_file": stored_file,
        "application_type": application_type, "application": application,
        "certificate": certificate,
        "hostel_building": building, "hostel_floor": floor, "hostel_room_type": room_type,
        "hostel_room": hostel_room, "hostel_bed": bed, "hostel_bed2": bed2, "hostel_allocation": hostel_allocation,
        "hostel_complaint": complaint,
        "transport_vehicle": vehicle, "transport_document": vehicle_document, "transport_driver": driver,
        "transport_route": bus_route, "transport_stop": stop, "transport_assignment": ride,
        "transport_trip": trip, "transport_record": trip_record, "transport_maintenance": vehicle_maintenance,
        "transport_fuel": fuel_log,
        "hr_position": position, "hr_contract": contract, "hr_profile": profile, "hr_document": document,
        "hr_fiscal_year": fiscal_year, "hr_leave_type": leave_type, "hr_leave_balance": leave_balance,
        "hr_leave_request": leave_request, "payroll_settings": payroll_settings,
        "payroll_component": pay_component, "payroll_structure": salary_structure,
        # The salary no payslip used comes first, so the PATCH sweep edits one it may.
        "payroll_salary": staff_salary, "payroll_old_salary": old_salary, "payroll_tax_scheme": tax_scheme,
        "payroll_run": payroll_run, "payroll_payslip": payslip, "payroll_adjustment": payroll_adjustment,
        "grade_scale": scale, "exam_type": exam_type, "exam": exam, "exam_subject": paper,
        "exam_component": component, "exam_room": exam_room, "seat_allocation": seat,
        "invigilation": invigilation, "admit_card": admit_card, "mark_sheet": sheet, "mark": mark,
        "mark_correction": mark_correction, "result": result, "result_plan": plan,
        "fee_category": fee_category, "fee_structure": fee_structure, "fee_structure_item": fee_structure_item,
        "scholarship": scholarship, "student_scholarship": student_scholarship, "invoice": invoice,
        "invoice_item": invoice_item, "installment": installment, "payment": payment, "receipt": receipt,
        "refund": refund,
        "event_category": event_category, "event": my_event, "event_registration": event_registration,
        "event_attendance": event_attendance, "event_participation": event_participation,
        "point_rule": point_rule, "point_entry": point_entry, "student_points": student_points,
        "award": award, "award_rule": award_rule, "student_award": student_award,
        "notification": notification, "notice": notice, "message_thread": message_thread, "message": message,
        "appointment_slot": appointment_slot, "appointment": appointment, "support_ticket": support_ticket,
        "ticket_comment": ticket_comment, "comm_other_user": other_comm_user,
        "library_author": author, "library_category": category, "library_publisher": publisher,
        "library_book": book, "library_shelf": shelf, "library_copy": copy, "library_member": member,
        "library_issue": issue, "library_fine": fine, "library_reservation": reservation,
        "inventory_category": item_category, "inventory_supplier": supplier, "inventory_item": stock_item,
        "inventory_asset_item": asset_item, "inventory_store": store, "inventory_store2": store2,
        "inventory_level": stock_level, "inventory_movement": stock_movement,
        "inventory_transfer": stock_transfer, "inventory_stock_issue": stock_issue,
        "inventory_stock_issue_line": stock_issue_line, "inventory_po": purchase_order,
        "inventory_po_line": purchase_line, "inventory_asset": asset,
        "inventory_assignment": asset_assignment, "inventory_maintenance": maintenance,
        "inventory_old_asset": old_asset, "inventory_disposal": disposal,
        "organization": org, "campus": campus, "user": user, "role": role, "staff": staff,
        "department": department, "program": program, "subject": subject, "elective": elective,
        "curriculum": curriculum, "academic_year": year, "term": term, "room": room,
        "batch": batch, "section": section, "teaching_assignment": assignment,
        "student": student, "enrollment": enrollment, "student_elective": student_elective,
        "parent": parent, "admission": admission, "calendar_event": event,
        "schedule": schedule, "period": period, "timetable_entry": entry,
        "lesson_change": change, "audit_log": audit,
        "attendance_session": roll_call, "lesson_session": lesson_session,
        "attendance_record": record, "attendance_correction": correction,
        "work_schedule": work_schedule, "staff_schedule": staff_schedule, "device": device,
        "biometric_identity": identity, "punch": punch, "staff_day": staff_day,
    }


def by_model(tenant):
    """The first row of each model, e.g. ``subject`` rather than ``elective``."""
    rows = {}
    for obj in tenant.values():
        rows.setdefault(type(obj), obj)
    return rows


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Route:
    pattern: str
    view: type
    actions: dict  # http method -> viewset action

    @property
    def is_detail(self):
        return "{pk}" in self.pattern

    @property
    def model(self):
        return self.view.queryset.model

    def url(self, pk=None):
        return self.pattern.format(pk=pk)

    def __str__(self):
        return f"{self.view.__name__} {self.pattern}"


def _walk(patterns, prefix=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _walk(pattern.url_patterns, prefix + str(pattern.pattern))
        else:
            yield prefix + str(pattern.pattern), pattern


def _to_url(regex):
    url = re.sub(r"\(\?P<pk>[^)]*\)", "{pk}", regex).replace("^", "").replace("$", "")
    return "/" + url


def api_routes():
    """Every viewset route of /api/v1/, plus the names of other views found."""
    routes, other_views = [], set()
    for regex, pattern in _walk(get_resolver().url_patterns):
        if not regex.startswith("api/v1/") or "<format>" in regex:
            continue
        view = getattr(pattern.callback, "cls", None)
        actions = getattr(pattern.callback, "actions", None)
        if view is None or not actions:
            other_views.add(view.__name__ if view else pattern.callback.__name__)
            continue
        # DRF adds "head" (a copy of "get") to the shared dict on first use.
        actions = {method: action for method, action in actions.items() if method != "head"}
        routes.append(Route(_to_url(regex), view, actions))
    return routes, other_views


# ---------------------------------------------------------------------------
# Actions with record ids in the body. The generic sweep can't see these
# serializers (they're built inside the action), so each foreign id is listed
# here, in an otherwise valid request. ``a``/``b`` are the two tenants.
# ---------------------------------------------------------------------------
def _future():
    return (date.today() + timedelta(days=14)).isoformat()


ACTION_ATTACKS = {
    ("StudentViewSet", "transfer"): [
        lambda a, b: ("student", {"campus": b["campus"].pk}),
    ],
    ("StudentViewSet", "place"): [
        lambda a, b: ("student", {"section": b["section"].pk}),
    ],
    ("ParentViewSet", "link_student"): [
        lambda a, b: ("parent", {"student": b["student"].pk, "relationship": "mother"}),
    ],
    ("ParentViewSet", "unlink_student"): [
        lambda a, b: ("parent", {"student": b["student"].pk}),
    ],
    ("UserViewSet", "assign_role"): [
        lambda a, b: ("user", {"role": b["role"].pk}),
        lambda a, b: ("user", {"role": a["role"].pk, "campus": b["campus"].pk}),
    ],
    ("UserViewSet", "revoke_role"): [
        lambda a, b: ("user", {"role": b["role"].pk}),
    ],
    ("SectionViewSet", "promote"): [
        lambda a, b: ("section", {"to_section": b["section"].pk}),
    ],
    ("BellScheduleViewSet", "retime"): [
        lambda a, b: ("schedule", {"effective_from": _future(), "periods": [
            {"period": b["period"].pk, "start_time": "09:00", "end_time": "09:45"}]}),
    ],
    ("TimetableEntryViewSet", "hand_over"): [
        lambda a, b: (None, {"teaching_assignments": [b["teaching_assignment"].pk],
                             "teacher": a["staff"].pk}),
        lambda a, b: (None, {"teaching_assignments": [a["teaching_assignment"].pk],
                             "teacher": b["staff"].pk}),
    ],
    ("TimetableEntryViewSet", "generate"): [
        # A dry run saves nothing but would still show org B's classes.
        lambda a, b, dry_run=dry_run: (None, {"sections": [b["section"].pk],
                                              "schedule": a["schedule"].pk,
                                              "days": [1, 2, 3, 4, 5], "dry_run": dry_run})
        for dry_run in (True, False)
    ] + [
        lambda a, b: (None, {"sections": [a["section"].pk], "schedule": b["schedule"].pk,
                             "days": [1, 2, 3, 4, 5], "dry_run": False}),
        lambda a, b: (None, {"sections": [a["section"].pk], "schedule": a["schedule"].pk,
                             "days": [1, 2, 3, 4, 5], "term": b["term"].pk, "dry_run": False}),
    ],
}

# Id fields checked in ``validate()``, which only runs once every field is
# valid: the create sweep sends these with the rest of a valid request.
CREATE_CONTEXT = {
    ("StudentElectiveViewSet", "in_section"):
        lambda a: {"student": a["student"].pk, "subject": a["elective"].pk},
}

def _session_token(session):
    return attendance_qr.issue("session", organization_id=session.organization_id,
                               target_id=session.pk)[0]


def _staff_token(campus):
    return attendance_qr.issue("staff", organization_id=campus.organization_id,
                               target_id=campus.pk)[0]


ACTION_ATTACKS.update({
    ("AttendanceSessionViewSet", "mark"): [
        lambda a, b: ("attendance_session", {"records": [
            {"enrollment": b["enrollment"].pk, "status": "absent"}]}),
    ],
    # A code shown in another organization's classroom.
    ("AttendanceSessionViewSet", "scan"): [
        lambda a, b: (None, {"token": _session_token(b["attendance_session"])}),
    ],
    ("PunchViewSet", "qr"): [
        lambda a, b: (None, {"campus": b["campus"].pk}),
    ],
    ("PunchViewSet", "check_in"): [
        lambda a, b: (None, {"token": _staff_token(b["campus"])}),
    ],
})

ACTION_ATTACKS.update({
    # A payment against another organization's invoice.
    ("PaymentViewSet", "create"): [
        lambda a, b: (None, {"invoice": b["invoice"].pk, "amount": "10", "method": "cash"}),
    ],
    ("FeeStructureViewSet", "generate_term_invoices"): [
        lambda a, b: ("fee_structure", {"term": b["term"].pk}),
        lambda a, b: ("fee_structure", {"term": a["term"].pk, "section": b["section"].pk}),
    ],
    ("FeeStructureViewSet", "generate_one_time_invoice"): [
        lambda a, b: ("fee_structure", {"student": b["student"].pk}),
    ],
    ("InvoiceViewSet", "add_item"): [
        lambda a, b: ("invoice", {"kind": "fine", "description": "x", "amount": "10",
                                  "category": b["fee_category"].pk}),
    ],
    ("EventViewSet", "mark_attendance"): [
        lambda a, b: ("event", {"entries": [{"student": b["student"].pk, "status": "present"}]}),
    ],
    ("PointEntryViewSet", "create"): [
        lambda a, b: (None, {"student": b["student"].pk, "points": "10", "reason": "x"}),
    ],
    ("StudentAwardViewSet", "create"): [
        lambda a, b: (None, {"student": b["student"].pk, "award": a["award"].pk}),
        lambda a, b: (None, {"student": a["student"].pk, "award": b["award"].pk}),
    ],
    ("EventViewSet", "record_participation"): [
        lambda a, b: ("event", {"student": b["student"].pk, "role": "participant"}),
    ],
    # Opening a sheet for another organization's paper or class.
    ("MarkSheetViewSet", "create"): [
        lambda a, b: (None, {"exam_subject": b["exam_subject"].pk, "section": a["section"].pk}),
        lambda a, b: (None, {"exam_subject": a["exam_subject"].pk, "section": b["section"].pk}),
    ],
    # Marks for another organization's students or components.
    ("MarkSheetViewSet", "marks"): [
        lambda a, b: ("mark_sheet", {"entries": [{"enrollment": b["enrollment"].pk,
                                                  "component": a["exam_component"].pk,
                                                  "status": "present", "marks": "50"}]}),
        lambda a, b: ("mark_sheet", {"entries": [{"enrollment": a["enrollment"].pk,
                                                  "component": b["exam_component"].pk,
                                                  "status": "present", "marks": "50"}]}),
    ],
    ("ExamViewSet", "generate_admit_cards"): [
        lambda a, b: ("exam", {"section": b["section"].pk}),
    ],
    ("SupportTicketViewSet", "assign"): [
        lambda a, b: ("support_ticket", {"assigned_to": b["user"].pk}),
    ],
    # A custom serializer (StartThreadSerializer/BookAppointmentSerializer) bypasses the
    # generic create-attack, which only inspects the read-only serializer_class — attacked here
    # by hand, the same way PointEntryViewSet/StudentAwardViewSet/MarkSheetViewSet are.
    ("MessageThreadViewSet", "create"): [
        lambda a, b: (None, {"other_user": b["comm_other_user"].pk, "campus": a["campus"].pk}),
        lambda a, b: (None, {"other_user": a["comm_other_user"].pk, "campus": b["campus"].pk}),
    ],
    ("AppointmentViewSet", "create"): [
        lambda a, b: (None, {"slot": b["appointment_slot"].pk}),
        lambda a, b: (None, {"slot": a["appointment_slot"].pk, "student": b["student"].pk}),
    ],
    # IssueBookSerializer/ReserveBookSerializer, same reason as above.
    ("IssueViewSet", "create"): [
        lambda a, b: (None, {"copy": b["library_copy"].pk, "member": a["library_member"].pk}),
        lambda a, b: (None, {"copy": a["library_copy"].pk, "member": b["library_member"].pk}),
    ],
    ("ReservationViewSet", "create"): [
        lambda a, b: (None, {"book": b["library_book"].pk, "member": a["library_member"].pk}),
        lambda a, b: (None, {"book": a["library_book"].pk, "member": b["library_member"].pk}),
    ],
    # Phase 10: every inventory write goes through a plain input serializer, so each
    # foreign id is attacked by hand, nested line ids included.
    ("StockLevelViewSet", "adjust"): [
        lambda a, b: (None, {"item": b["inventory_item"].pk, "store": a["inventory_store"].pk, "delta": 1,
                             "reason": "x"}),
        lambda a, b: (None, {"item": a["inventory_item"].pk, "store": b["inventory_store"].pk, "delta": 1,
                             "reason": "x"}),
    ],
    ("StockTransferViewSet", "create"): [
        lambda a, b: (None, {"item": b["inventory_item"].pk, "from_store": a["inventory_store"].pk,
                             "to_store": a["inventory_store2"].pk, "quantity": 1}),
        lambda a, b: (None, {"item": a["inventory_item"].pk, "from_store": b["inventory_store"].pk,
                             "to_store": a["inventory_store2"].pk, "quantity": 1}),
        lambda a, b: (None, {"item": a["inventory_item"].pk, "from_store": a["inventory_store"].pk,
                             "to_store": b["inventory_store2"].pk, "quantity": 1}),
    ],
    ("StockIssueViewSet", "create"): [
        lambda a, b: (None, {"store": b["inventory_store"].pk, "staff": a["staff"].pk,
                             "lines": [{"item": a["inventory_item"].pk, "quantity": 1}]}),
        lambda a, b: (None, {"store": a["inventory_store"].pk, "staff": b["staff"].pk,
                             "lines": [{"item": a["inventory_item"].pk, "quantity": 1}]}),
        lambda a, b: (None, {"store": a["inventory_store"].pk, "department": b["department"].pk,
                             "lines": [{"item": a["inventory_item"].pk, "quantity": 1}]}),
        lambda a, b: (None, {"store": a["inventory_store"].pk, "staff": a["staff"].pk,
                             "lines": [{"item": b["inventory_item"].pk, "quantity": 1}]}),
    ],
    ("PurchaseOrderViewSet", "create"): [
        lambda a, b: (None, {"supplier": b["inventory_supplier"].pk, "store": a["inventory_store"].pk,
                             "lines": [{"item": a["inventory_item"].pk, "quantity": 1, "unit_price": "1"}]}),
        lambda a, b: (None, {"supplier": a["inventory_supplier"].pk, "store": b["inventory_store"].pk,
                             "lines": [{"item": a["inventory_item"].pk, "quantity": 1, "unit_price": "1"}]}),
        lambda a, b: (None, {"supplier": a["inventory_supplier"].pk, "store": a["inventory_store"].pk,
                             "lines": [{"item": b["inventory_item"].pk, "quantity": 1, "unit_price": "1"}]}),
    ],
    ("PurchaseOrderViewSet", "receive"): [
        lambda a, b: ("inventory_po", {"lines": [{"line": b["inventory_po_line"].pk, "quantity": 1}]}),
    ],
    ("AssetViewSet", "create"): [
        lambda a, b: (None, {"item": b["inventory_asset_item"].pk, "store": a["inventory_store"].pk}),
        lambda a, b: (None, {"item": a["inventory_asset_item"].pk, "store": b["inventory_store"].pk}),
    ],
    ("AssetViewSet", "assign"): [
        lambda a, b: ("inventory_asset", {"staff": b["staff"].pk}),
        lambda a, b: ("inventory_asset", {"student": b["student"].pk}),
        lambda a, b: ("inventory_asset", {"room": b["room"].pk}),
        lambda a, b: ("inventory_asset", {"department": b["department"].pk}),
    ],
    ("AssetViewSet", "move"): [
        lambda a, b: ("inventory_asset", {"store": b["inventory_store"].pk}),
    ],
    ("MaintenanceViewSet", "create"): [
        lambda a, b: (None, {"asset": b["inventory_asset"].pk, "kind": "repair", "description": "x"}),
        lambda a, b: (None, {"asset": a["inventory_asset"].pk, "kind": "repair", "description": "x",
                             "supplier": b["inventory_supplier"].pk}),
    ],
})

ACTION_ATTACKS.update({
    # Phase 11: leave requests, balances and runs go through plain input serializers.
    ("LeaveRequestViewSet", "create"): [
        lambda a, b: (None, {"staff": b["staff"].pk, "leave_type": a["hr_leave_type"].pk,
                             "start_date": _future(), "end_date": _future()}),
        lambda a, b: (None, {"staff": a["staff"].pk, "leave_type": b["hr_leave_type"].pk,
                             "start_date": _future(), "end_date": _future()}),
    ],
    ("LeaveRequestViewSet", "me"): [
        lambda a, b: (None, {"leave_type": b["hr_leave_type"].pk, "start_date": _future(),
                             "end_date": _future()}),
    ],
    ("LeaveBalanceViewSet", "open"): [
        lambda a, b: (None, {"fiscal_year": b["hr_fiscal_year"].pk}),
        lambda a, b: (None, {"fiscal_year": a["hr_fiscal_year"].pk, "campus": b["campus"].pk}),
    ],
    ("PayrollRunViewSet", "create"): [
        lambda a, b: (None, {"campus": b["campus"].pk, "name": "x", "period_start": _future(),
                             "period_end": _future()}),
    ],
})

ACTION_ATTACKS.update({
    # Phase 12: allocations, complaints, rider assignments and trips go through plain input serializers.
    ("AllocationViewSet", "create"): [
        lambda a, b: (None, {"bed": b["hostel_bed2"].pk, "student": a["student"].pk}),
        lambda a, b: (None, {"bed": a["hostel_bed2"].pk, "student": b["student"].pk}),
        lambda a, b: (None, {"bed": a["hostel_bed2"].pk, "staff": b["staff"].pk}),
    ],
    ("AllocationViewSet", "move"): [
        lambda a, b: ("hostel_allocation", {"bed": b["hostel_bed2"].pk}),
    ],
    ("AllocationViewSet", "generate_invoices"): [
        lambda a, b: (None, {"term": b["term"].pk}),
        lambda a, b: (None, {"term": a["term"].pk, "building": b["hostel_building"].pk}),
    ],
    ("ComplaintViewSet", "create"): [
        lambda a, b: (None, {"building": b["hostel_building"].pk, "title": "x"}),
        lambda a, b: (None, {"building": a["hostel_building"].pk, "room": b["hostel_room"].pk, "title": "x"}),
    ],
    ("ComplaintViewSet", "assign"): [
        lambda a, b: ("hostel_complaint", {"staff": b["staff"].pk}),
    ],
    ("AssignmentViewSet", "create"): [
        lambda a, b: (None, {"route": b["transport_route"].pk, "stop": a["transport_stop"].pk,
                             "staff": a["staff"].pk}),
        lambda a, b: (None, {"route": a["transport_route"].pk, "stop": b["transport_stop"].pk,
                             "staff": a["staff"].pk}),
        lambda a, b: (None, {"route": a["transport_route"].pk, "stop": a["transport_stop"].pk,
                             "student": b["student"].pk}),
        lambda a, b: (None, {"route": a["transport_route"].pk, "stop": a["transport_stop"].pk,
                             "staff": b["staff"].pk}),
    ],
    ("AssignmentViewSet", "generate_invoices"): [
        lambda a, b: (None, {"term": b["term"].pk}),
        lambda a, b: (None, {"term": a["term"].pk, "route": b["transport_route"].pk}),
    ],
    ("TripViewSet", "create"): [
        lambda a, b: (None, {"route": b["transport_route"].pk, "direction": "drop"}),
    ],
    ("TripViewSet", "mark"): [
        lambda a, b: ("transport_trip", {"entries": [{"assignment": b["transport_assignment"].pk,
                                                      "status": "absent"}]}),
    ],
})

ACTION_ATTACKS.update({
    # Phase 13: the submission and decision serializers are plain input; ids inside ``data`` and
    # ``decision`` are checked by each kind (applications/kinds.py).
    ("ApplicationViewSet", "create"): [
        lambda a, b: (None, {"application_type": b["application_type"].pk, "student": a["student"].pk,
                             "data": {}}),
        lambda a, b: (None, {"application_type": a["application_type"].pk, "student": b["student"].pk,
                             "data": {}}),
        lambda a, b: (None, {"application_type": a["application_type"].pk, "student": a["student"].pk,
                             "data": {"building": b["hostel_building"].pk}}),
        lambda a, b: (None, {"application_type": a["application_type"].pk, "student": a["student"].pk,
                             "data": {"room_type": b["hostel_room_type"].pk}}),
    ],
    ("ApplicationViewSet", "approve"): [
        lambda a, b: ("application", {"decision": {"bed": b["hostel_bed2"].pk}}),
    ],
    ("CertificateViewSet", "create"): [
        lambda a, b: (None, {"student": b["student"].pk, "title": "x"}),
    ],
})

ACTION_ATTACKS.update({
    # API keys: role grants name a role and a campus, both checked against the key's organization.
    ("ApiKeyViewSet", "assign_role"): [
        lambda a, b: ("api_key", {"role": b["role"].pk}),
        lambda a, b: ("api_key", {"role": a["role"].pk, "campus": b["campus"].pk}),
    ],
    ("ApiKeyViewSet", "revoke_role"): [
        lambda a, b: ("api_key", {"role": b["role"].pk}),
    ],
})

ACTION_ATTACKS.update({
    # Phase 14: plain input serializers with record ids.
    ("AlumniProfileViewSet", "graduate"): [
        lambda a, b: (None, {"section": b["section"].pk}),
        lambda a, b: (None, {"students": [b["student"].pk]}),
    ],
    ("MentorshipViewSet", "create"): [
        lambda a, b: (None, {"mentor": b["alumni_profile"].pk, "topic": "x"}),
    ],
    ("DonationViewSet", "create"): [
        lambda a, b: (None, {"campus": b["campus"].pk, "donor_name": "x", "amount": "10"}),
        lambda a, b: (None, {"campus": a["campus"].pk, "campaign": b["alumni_campaign"].pk, "donor_name": "x",
                             "amount": "10"}),
        lambda a, b: (None, {"campus": a["campus"].pk, "donor": b["alumni_profile"].pk, "amount": "10"}),
    ],
    ("VacancyViewSet", "apply"): [
        lambda a, b: ("careers_vacancy", {"data": {"first_name": "x", "last_name": "y", "phone": "1"},
                                          "resume_file": b["stored_file"].pk}),
    ],
    ("InterviewViewSet", "create"): [
        lambda a, b: (None, {"candidacy": b["careers_candidacy"].pk, "scheduled_at": _future() + "T10:00:00Z"}),
        lambda a, b: (None, {"candidacy": a["careers_candidacy"].pk, "scheduled_at": _future() + "T10:00:00Z",
                             "panel": [b["staff"].pk]}),
    ],
    ("JobOfferViewSet", "create"): [
        lambda a, b: (None, {"candidacy": b["careers_candidacy"].pk, "start_date": _future()}),
    ],
})

# Write actions whose body names no other record, so the detail sweep (a
# foreign pk in the URL) is all there is to attack.
NO_RECORD_INPUT = {
    ("StudentViewSet", "change_status"),
    ("AdmissionViewSet", "approve"),
    ("AdmissionViewSet", "reject"),
    ("AdmissionViewSet", "withdraw"),
    ("AdmissionViewSet", "enroll"),
    ("UserViewSet", "set_password"),
    ("UserViewSet", "deactivate"),
    ("UserViewSet", "reset_two_factor"),
    ("AcademicYearViewSet", "set_current"),
    ("AttendanceSessionViewSet", "submit"),
    ("AttendanceSessionViewSet", "reopen"),
    ("AttendanceSessionViewSet", "qr"),
    ("AttendanceDeviceViewSet", "rotate_key"),
    ("ExamViewSet", "add_curriculum"),
    ("ExamViewSet", "schedule"),
    ("ExamViewSet", "unschedule"),
    ("ExamViewSet", "publish"),
    ("ExamViewSet", "unpublish"),
    ("ExamViewSet", "compute"),
    ("ExamViewSet", "seat_plan"),
    ("ExamViewSet", "clear_seat_plan"),
    ("AdmitCardViewSet", "withhold"),
    ("AdmitCardViewSet", "release"),
    ("MarkSheetViewSet", "submit"),
    ("MarkSheetViewSet", "verify"),
    ("MarkSheetViewSet", "send_back"),
    ("ResultViewSet", "remark"),
    ("ResultPlanViewSet", "compute"),
    ("ResultPlanViewSet", "publish"),
    ("ResultPlanViewSet", "unpublish"),
    ("StudentScholarshipViewSet", "end"),
    ("InvoiceViewSet", "cancel"),
    ("InvoiceViewSet", "set_installments"),
    ("PaymentViewSet", "refund"),
    ("EventViewSet", "publish"),
    ("EventViewSet", "cancel"),
    ("EventViewSet", "register"),
    ("EventRegistrationViewSet", "decide"),
    ("EventRegistrationViewSet", "withdraw"),
    ("StudentAwardViewSet", "end"),
    ("NoticeViewSet", "publish"),
    ("NotificationViewSet", "mark_read"),
    ("NotificationViewSet", "mark_all_read"),
    ("MessageThreadViewSet", "messages"),
    ("MessageThreadViewSet", "close"),
    ("AppointmentSlotViewSet", "cancel"),
    ("AppointmentViewSet", "approve"),
    ("AppointmentViewSet", "cancel"),
    ("AppointmentViewSet", "complete"),
    ("SupportTicketViewSet", "resolve"),
    ("SupportTicketViewSet", "close"),
    ("SupportTicketViewSet", "comments"),
    ("CopyViewSet", "withdraw"),
    ("MemberViewSet", "deactivate"),
    ("IssueViewSet", "return_copy"),
    ("ReservationViewSet", "cancel"),
    ("ReservationViewSet", "fulfil"),
    ("ReservationViewSet", "expire_stale"),
    ("FineViewSet", "pay"),
    ("FineViewSet", "waive"),
    ("PurchaseOrderViewSet", "place"),
    ("PurchaseOrderViewSet", "cancel"),
    ("AssetViewSet", "return_asset"),
    ("AssetViewSet", "dispose"),
    ("MaintenanceViewSet", "start"),
    ("MaintenanceViewSet", "complete"),
    ("MaintenanceViewSet", "cancel"),
    ("ContractViewSet", "end"),
    ("LeaveBalanceViewSet", "adjust"),
    ("LeaveRequestViewSet", "approve"),
    ("LeaveRequestViewSet", "reject"),
    ("LeaveRequestViewSet", "cancel"),
    ("PayrollRunViewSet", "compute"),
    ("PayrollRunViewSet", "approve"),
    ("PayrollRunViewSet", "mark_paid"),
    ("PayrollRunViewSet", "cancel"),
    ("PayslipViewSet", "set_overtime"),
    ("AllocationViewSet", "check_in"),
    ("AllocationViewSet", "check_out"),
    ("AllocationViewSet", "cancel"),
    ("ComplaintViewSet", "resolve"),
    ("ComplaintViewSet", "reject"),
    # Resident's own complaint: the building and room come from their own bed.
    ("ComplaintViewSet", "me"),
    ("AssignmentViewSet", "end"),
    ("TripViewSet", "complete"),
    ("ApplicationViewSet", "reject"),
    ("ApplicationViewSet", "send_back"),
    ("ApplicationViewSet", "withdraw"),
    # Its body does carry ids (``data``), but only the applicant may resubmit, and the attacker
    # fixture never is one: it gets 403 before the body is read. The same ``kinds.clean_data``
    # check is attacked through ``create`` above.
    ("ApplicationViewSet", "resubmit"),
    ("CertificateViewSet", "revoke"),
    ("ApiKeyViewSet", "revoke"),
    ("ApiKeyViewSet", "rotate"),
    # Platform admins only: refused (403) before any lookup.
    ("SignupRequestViewSet", "approve"),
    ("SignupRequestViewSet", "reject"),
    # Phase 14
    ("AlumniProfileViewSet", "me"),
    ("AlumniEventViewSet", "publish"),
    ("AlumniEventViewSet", "cancel"),
    ("AlumniEventViewSet", "rsvp"),
    ("MentorshipViewSet", "accept"),
    ("MentorshipViewSet", "decline"),
    ("MentorshipViewSet", "end"),
    ("DonationViewSet", "refund"),
    ("VacancyViewSet", "open"),
    ("VacancyViewSet", "close"),
    ("CandidacyViewSet", "screen"),
    ("InterviewViewSet", "reschedule"),
    ("InterviewViewSet", "cancel"),
    ("InterviewViewSet", "outcome"),
    ("JobOfferViewSet", "withdraw"),
    ("JobOfferViewSet", "respond"),
    ("JobPostingViewSet", "review"),
    ("JobPostingViewSet", "close"),
}

# Collections where an empty request legitimately returns nothing for the
# all-permissions attacker fixture, so the "own rows are visible" assertion
# doesn't apply — ReportCardViewSet needs a query to say what to list at all;
# NotificationViewSet/MessageThreadViewSet are scoped to "am I the recipient/a
# participant", not to a permission code, and the attacker fixture is neither.
# The leak checks still run either way.
LIST_NEEDS_INPUT = {"ReportCardViewSet", "NotificationViewSet", "MessageThreadViewSet",
                    # "My uploads": scoped to the uploader, not a permission.
                    "StoredFileViewSet",
                    # Platform admins only: an organization's admin is refused outright.
                    "SignupRequestViewSet"}

# Writable nested serializers with no record ids inside, so there is nothing
# to smuggle. Ones that do carry ids are attacked by hand (NESTED_ATTACKS).
NESTED_WITHOUT_IDS = {
    ("GradeScaleSerializer", "bands"), ("GradeScaleSerializer", "divisions"),
    ("ExamSubjectSerializer", "components"), ("TaxSchemeSerializer", "slabs"),
    ("ApplicationTypeSerializer", "steps"),
}
NESTED_WITH_IDS = {("ResultPlanSerializer", "items"), ("FeeStructureSerializer", "items"),
                   ("SalaryStructureSerializer", "lines"), ("StaffSalarySerializer", "lines")}


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------
def _tenant_models():
    """(model, path to its organization id) for every tenant model."""
    for model in apps.get_models():
        names = {f.name for f in model._meta.get_fields()}
        if "organization" in names and model is not Organization:
            yield model, "organization_id"
        elif model._meta.label == "rbac.UserRole":
            yield model, "user__organization_id"


def snapshot(organization_id):
    """Every row that belongs to one organization, deleted ones included."""
    rows = {
        "organizations.Organization": list(
            Organization._base_manager.filter(pk=organization_id).values()
        )
    }
    for model, org_path in _tenant_models():
        rows[model._meta.label] = list(
            model._base_manager.filter(**{org_path: organization_id}).order_by("pk").values()
        )
    return rows


def cross_tenant_references():
    """Rows that point at a row of another organization, as readable strings."""
    found = []
    for model, org_path in _tenant_models():
        for field in model._meta.fields:
            related = field.related_model
            if not field.is_relation or related is Organization:
                continue
            related_names = {f.name for f in related._meta.get_fields()}
            if "organization" not in related_names:
                continue
            bad = (
                model._base_manager
                .filter(**{f"{org_path}__isnull": False,
                           f"{field.name}__organization_id__isnull": False})
                .exclude(**{f"{field.name}__organization_id": F(org_path)})
            )
            for row in bad.values("pk", org_path, f"{field.name}__organization_id"):
                found.append(f"{model._meta.label}#{row['pk']}.{field.name} -> org "
                             f"{row[f'{field.name}__organization_id']} (row's org {row[org_path]})")
    return found


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------
class TenantSweepTestCase(APITestCaseBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.a = build_tenant(ATTACKER_TAG)
        cls.b = build_tenant(MARK)
        cls.a_by_model = by_model(cls.a)
        cls.b_by_model = by_model(cls.b)
        cls.attacker = user_with_permissions(
            cls.a["organization"], Permission.objects.values_list("code", flat=True),
            email=f"attacker@{ATTACKER_TAG}.test",
        )
        cls.routes, cls.other_views = api_routes()

    def setUp(self):
        self.authenticate(self.attacker)
        self.b_before = snapshot(self.b["organization"].pk)

    def attack(self, method, url, data=None, query=None):
        """Send one request, check the invariants, then roll everything back.

        Returns the response, and a list of problems (empty when it held)."""
        sid = transaction.savepoint()
        try:
            if query is not None:
                response = self.client.get(url, query)
            else:
                response = getattr(self.client, method)(url, data or {}, format="json")
            problems = []
            if MARK in response.content.decode(errors="replace").lower():
                problems.append(f"response shows org B data: {response.content[:300]!r}")
            if snapshot(self.b["organization"].pk) != self.b_before:
                problems.append("org B's data changed")
            problems += cross_tenant_references()
            return response, problems
        finally:
            transaction.savepoint_rollback(sid)

    def assert_held(self, label, response, problems, expected=None):
        if expected is not None and response.status_code not in expected:
            problems = [f"status {response.status_code}, expected {sorted(expected)}: "
                        f"{response.content[:300]!r}"] + problems
        self.assertEqual(problems, [], label)


class SweepGuardTests(TenantSweepTestCase):
    """Fail when a new endpoint appears that the sweep can't attack."""

    def test_fixtures_are_consistent(self):
        self.assertEqual(cross_tenant_references(), [])

    def test_every_tenant_model_has_a_victim_row(self):
        missing = [model._meta.label for model, org_path in _tenant_models()
                   if not model._base_manager.filter(
                       **{org_path: self.b["organization"].pk}).exists()]
        self.assertEqual(missing, [], "Add a row of these to build_tenant().")

    def test_every_viewset_model_has_a_victim_row(self):
        missing = {r.model.__name__ for r in self.routes
                   if r.model not in self.b_by_model and r.model not in GLOBAL_MODELS}
        self.assertEqual(missing, set(), "Add a row of these to build_tenant().")

    def test_every_other_view_is_known(self):
        self.assertEqual(self.other_views - SELF_ONLY_VIEWS, set(),
                         "New non-viewset route: add a tenant test for it, then list it "
                         "in SELF_ONLY_VIEWS only if it acts on the caller alone.")

    def test_every_write_action_is_classified(self):
        unknown = {
            (r.view.__name__, action)
            for r in self.routes for method, action in r.actions.items()
            if method != "get" and action not in CRUD_ACTIONS
        } - set(ACTION_ATTACKS) - NO_RECORD_INPUT
        self.assertEqual(unknown, set(), "Add these to ACTION_ATTACKS or NO_RECORD_INPUT.")


MISSING_PK = 987654321


class ForeignIdInUrlTests(TenantSweepTestCase):
    def test_every_detail_route_hides_other_tenants_records(self):
        """Org B's record in the URL gets exactly the answer a record that
        doesn't exist gets — usually 404, or a refusal made before any lookup
        (e.g. 403 for an action only platform admins may take). Anything else
        tells the attacker the id is taken."""
        for route in self.routes:
            if not route.is_detail or route.model in GLOBAL_MODELS:
                continue
            victim = self.b_by_model[route.model]
            for method in route.actions:
                with self.subTest(route=str(route), method=method):
                    missing, _ = self.attack(method, route.url(MISSING_PK))
                    response, problems = self.attack(method, route.url(victim.pk))
                    if response.status_code < 400:
                        problems.insert(0, f"status {response.status_code}")
                    if (response.status_code, response.content) != (
                        missing.status_code, missing.content
                    ):
                        problems.insert(0, f"differs from a missing id: {response.status_code} "
                                           f"{response.content[:200]!r} vs {missing.status_code} "
                                           f"{missing.content[:200]!r}")
                    self.assert_held(f"{method.upper()} {route}", response, problems)


class ListTests(TenantSweepTestCase):
    def test_collections_show_only_own_records(self):
        for route in self.routes:
            if route.is_detail or "get" not in route.actions:
                continue
            with self.subTest(route=str(route)):
                response, problems = self.attack("get", route.url(), query={})
                if (route.actions["get"] == "list" and route.model not in GLOBAL_MODELS
                        and route.view.__name__ not in LIST_NEEDS_INPUT):
                    # Otherwise a 403 or an empty page would pass without testing anything.
                    self.assertEqual(response.status_code, 200, response.content[:300])
                    self.assertIn(ATTACKER_TAG, response.content.decode().lower(),
                                  f"{route}: the attacker's own rows are missing")
                self.assert_held(f"GET {route}", response, problems)

    def test_filtering_by_another_tenants_id_reveals_nothing(self):
        for route in self.routes:
            if route.is_detail or "get" not in route.actions:
                continue
            for param, key in QUERY_PARAMS.items():
                with self.subTest(route=str(route), param=param):
                    response, problems = self.attack(
                        "get", route.url(), query={param: self.b[key].pk}
                    )
                    self.assert_held(f"GET {route}?{param}=<org B>", response, problems)


def writable_relations(serializer):
    """(field name, related model, many) for every writable id field."""
    for name, field in serializer.fields.items():
        if field.read_only:
            continue
        if isinstance(field, serializers.BaseSerializer) or isinstance(getattr(field, "child", None), serializers.BaseSerializer):
            if (type(serializer).__name__, name) in NESTED_WITHOUT_IDS | NESTED_WITH_IDS:
                continue
        if isinstance(field, ManyRelatedField):
            yield name, field.child_relation.queryset.model, True
        elif isinstance(field, RelatedField):
            yield name, field.queryset.model, False
        elif isinstance(field, serializers.BaseSerializer):
            raise AssertionError(
                f"{type(serializer).__name__}.{name} is a writable nested serializer; "
                "the sweep doesn't reach inside it. Add an attack for it by hand."
            )


class ForeignIdInBodyTests(TenantSweepTestCase):
    def serializer_for(self, route, action, method):
        request = getattr(APIRequestFactory(), method)("/")
        force_authenticate(request, user=self.attacker)
        view = route.view(action_map={method: action}, format_kwarg=None, args=(), kwargs={})
        view.request = view.initialize_request(request)
        return view.get_serializer()

    def test_model_serializers_refuse_other_tenants_ids(self):
        """PATCH the attacker's own record with each id field pointing at org B."""
        for route in self.routes:
            if not route.is_detail or route.actions.get("patch") != "partial_update":
                continue
            own = self.a_by_model[route.model]
            probe, _ = self.attack("patch", route.url(own.pk), {})
            if probe.status_code >= 400:
                continue  # the attacker can't edit even their own, so nothing to smuggle
            serializer = self.serializer_for(route, "partial_update", "patch")
            for name, related, many in writable_relations(serializer):
                if related in GLOBAL_MODELS:
                    continue
                with self.subTest(route=str(route), field=name):
                    self.assertIn(related, self.b_by_model,
                                  f"No org B {related.__name__}: add one to build_tenant().")
                    foreign = self.b_by_model[related].pk
                    response, problems = self.attack(
                        "patch", route.url(own.pk), {name: [foreign] if many else foreign}
                    )
                    self.assert_held(f"PATCH {route} {name}=<org B>", response, problems,
                                     {400, 404})

    def test_create_refuses_other_tenants_ids(self):
        """POST with each id field pointing at org B: that field must be refused.

        Usually only that field is sent, so the others fail as missing; the
        check is that the foreign id is named in the errors too. Fields in
        CREATE_CONTEXT get the rest of a valid request."""
        for route in self.routes:
            if route.is_detail or route.actions.get("post") != "create":
                continue
            serializer = self.serializer_for(route, "create", "post")
            for name, related, many in writable_relations(serializer):
                if related in GLOBAL_MODELS:
                    continue
                with self.subTest(route=str(route), field=name):
                    foreign = self.b_by_model[related].pk
                    context = CREATE_CONTEXT.get((route.view.__name__, name), lambda a: {})
                    body = {**context(self.a), name: [foreign] if many else foreign}
                    response, problems = self.attack("post", route.url(), body)
                    # A view whose create() refuses every POST outright (see AdmitCardViewSet,
                    # InvoiceViewSet) never validates the body at all: an
                    # ordinary attacker is blocked by HasPermission first (403, "create" isn't a
                    # declared permission), and even a superuser reaching create() gets 405 — both
                    # stricter refusals than a 400 naming the field, so both are held.
                    self.assert_held(f"POST {route} {name}=<org B>", response, problems, {400, 403, 405})
                    if response.status_code in (403, 405):
                        continue
                    details = response.data["error"]["details"] or {}
                    self.assertIn(name, details, f"POST {route}: {name}=<org B> not refused")

    def test_actions_refuse_other_tenants_ids(self):
        by_action = {(r.view.__name__, a): r for r in self.routes for a in r.actions.values()}
        for (view_name, action), attacks in ACTION_ATTACKS.items():
            route = by_action[(view_name, action)]
            for number, build in enumerate(attacks):
                own_key, body = build(self.a, self.b)
                url = route.url(self.a[own_key].pk) if own_key else route.url()
                with self.subTest(action=f"{view_name}.{action}", attack=number):
                    response, problems = self.attack("post", url, body)
                    self.assert_held(f"POST {url} {body}", response, problems, {400, 404})


class NestedIdTests(TenantSweepTestCase):
    """Ids inside a writable nested serializer, which the generic sweep skips."""

    def test_a_term_result_cannot_count_another_organizations_exam(self):
        a, b = self.a, self.b
        route = next(r for r in self.routes if r.view.__name__ == "ResultPlanViewSet" and not r.is_detail)
        own = {"campus": a["campus"].pk, "academic_year": a["academic_year"].pk, "program": a["program"].pk,
               "name": "Nested attack"}
        response, problems = self.attack("post", route.url(), {**own, "items": [
            {"exam": b["exam"].pk, "weight": "100"}]})
        self.assert_held("POST term-results items=<org B exam>", response, problems, {400})
        self.assertIn("items", response.data["error"]["details"])

        plan = a["result_plan"]
        response, problems = self.attack("patch", route.url() + f"{plan.pk}/", {"items": [
            {"exam": b["exam"].pk, "weight": "100"}]})
        self.assert_held("PATCH term-results items=<org B exam>", response, problems, {400})

    def test_a_fee_structure_cannot_use_another_organizations_category(self):
        a, b = self.a, self.b
        route = next(r for r in self.routes if r.view.__name__ == "FeeStructureViewSet" and not r.is_detail)
        own = {"program": a["program"].pk, "level": 11, "academic_year": a["academic_year"].pk, "name": "Nested attack"}
        response, problems = self.attack("post", route.url(), {**own, "items": [
            {"category": b["fee_category"].pk, "amount": "10", "frequency": "per_term"}]})
        self.assert_held("POST fee-structures items=<org B category>", response, problems, {400})
        self.assertIn("items", response.data["error"]["details"])

        structure = a["fee_structure"]
        response, problems = self.attack("patch", route.url() + f"{structure.pk}/", {"items": [
            {"category": b["fee_category"].pk, "amount": "10", "frequency": "per_term"}]})
        self.assert_held("PATCH fee-structures items=<org B category>", response, problems, {400})

    def test_a_salary_cannot_use_another_organizations_component(self):
        a, b = self.a, self.b
        route = next(r for r in self.routes if r.view.__name__ == "SalaryStructureViewSet" and not r.is_detail)
        lines = [{"component": b["payroll_component"].pk, "value": "10"}]
        response, problems = self.attack("post", route.url(), {"code": "nested", "name": "Nested", "basic": "1",
                                                               "lines": lines})
        self.assert_held("POST structures lines=<org B component>", response, problems, {400})
        self.assertIn("lines", response.data["error"]["details"])
        structure = a["payroll_structure"]
        response, problems = self.attack("patch", route.url() + f"{structure.pk}/", {"lines": lines})
        self.assert_held("PATCH structures lines=<org B component>", response, problems, {400})

        route = next(r for r in self.routes if r.view.__name__ == "StaffSalaryViewSet" and not r.is_detail)
        response, problems = self.attack("post", route.url(), {
            "staff": a["staff"].pk, "structure": a["payroll_structure"].pk, "effective_from": _future(),
            "lines": lines})
        self.assert_held("POST staff-salaries lines=<org B component>", response, problems, {400})
        self.assertIn("lines", response.data["error"]["details"])
        salary = a["payroll_salary"]
        response, problems = self.attack("patch", route.url() + f"{salary.pk}/", {"lines": lines})
        self.assert_held("PATCH staff-salaries lines=<org B component>", response, problems, {400})
