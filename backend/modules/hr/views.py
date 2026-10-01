from datetime import timedelta

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed, NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.common.exceptions import ConflictError
from core.common.mixins import CampusScopedViewSet, OrganizationScopedViewSet
from core.common.permissions import IsSameOrganization
from modules.staff.models import StaffMember
from modules.staff.selectors import staff_member_for_user

from . import services
from .models import (
    Contract,
    EmployeeProfile,
    FiscalYear,
    LeaveBalance,
    LeaveRequest,
    LeaveStatus,
    LeaveType,
    Position,
    StaffDocument,
)
from .serializers import (
    AdjustBalanceSerializer,
    ApplyLeaveSerializer,
    ContractSerializer,
    CreateLeaveRequestSerializer,
    DecideLeaveSerializer,
    EmployeeProfileSerializer,
    EndContractSerializer,
    FiscalYearSerializer,
    LeaveBalanceSerializer,
    LeaveRequestSerializer,
    LeaveTypeSerializer,
    OpenBalancesSerializer,
    PositionSerializer,
    RejectLeaveSerializer,
    StaffDocumentSerializer,
)
from .services import APPROVE, MANAGE, VIEW

TAG = "hr"
READ = {"list": [VIEW], "retrieve": [VIEW]}
CRUD = {**READ, "create": [MANAGE], "update": [MANAGE], "partial_update": [MANAGE], "destroy": [MANAGE]}


def _schema(noun: str, *actions):
    summaries = {"list": f"List {noun}s", "retrieve": f"Retrieve a {noun}",
                 "create": f"Create a {noun}", "update": f"Replace a {noun}",
                 "partial_update": f"Update a {noun}", "destroy": f"Delete a {noun}"}
    return extend_schema_view(**{a: extend_schema(tags=[TAG], summary=summaries[a])
                                 for a in actions or summaries})


def _own_staff(request) -> StaffMember:
    staff = staff_member_for_user(request.user)
    if staff is None:
        raise NotFound("No staff record is linked to your account.")
    return staff


class SelfServiceMixin:
    """``me`` works for any signed-in staff member, without a permission."""

    self_service_actions = ("me",)

    def get_permissions(self):
        if self.action in self.self_service_actions:
            return [IsAuthenticated()]
        return super().get_permissions()


class StaffRecordViewSet(SelfServiceMixin, CampusScopedViewSet):
    """HR records about one staff member, reaching a campus through them."""

    campus_field = "staff__campus"
    audit_module = "hr"

    def campus_of(self, validated_data):
        staff = validated_data.get("staff")
        return staff.campus if staff is not None else None

    def own_rows(self, request):
        return self.filter_queryset(self.queryset.model.objects.filter(staff=_own_staff(request)))


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------
@_schema("position")
class PositionViewSet(OrganizationScopedViewSet):
    queryset = Position.objects.all()
    serializer_class = PositionSerializer
    audit_module = "hr"
    filterset_fields = ["is_active"]
    search_fields = ["code", "name"]
    required_permissions = CRUD

    def perform_destroy(self, instance):
        if instance.contracts.exists():
            raise ConflictError("Contracts name this position; deactivate it instead.", code="in_use")
        super().perform_destroy(instance)


@_schema("contract")
class ContractViewSet(StaffRecordViewSet):
    """Employment terms, one after another. A contract that has started is
    history: end it (``end``) rather than deleting it."""

    queryset = Contract.objects.select_related("staff", "position", "department")
    serializer_class = ContractSerializer
    filterset_fields = ["staff", "kind", "position", "department"]
    search_fields = ["staff__first_name", "staff__last_name", "staff__employee_number", "reference"]
    required_permissions = {**CRUD, "end": [MANAGE]}

    def perform_destroy(self, instance):
        if instance.start_date <= timezone.localdate():
            raise ConflictError("This contract has started; end it instead.", code="started")
        super().perform_destroy(instance)

    @extend_schema(tags=[TAG], summary="End a contract on a date", request=EndContractSerializer,
                   responses={200: ContractSerializer})
    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        contract = self.get_object()
        serializer = EndContractSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        contract = services.end_contract(contract, by=request.user, **serializer.validated_data)
        return Response(ContractSerializer(contract).data)

    @extend_schema(tags=[TAG], summary="My contracts", responses={200: ContractSerializer(many=True)})
    @action(detail=False, methods=["get"])
    def me(self, request):
        return Response(ContractSerializer(self.own_rows(request), many=True).data)


@_schema("employee profile")
class EmployeeProfileViewSet(StaffRecordViewSet):
    queryset = EmployeeProfile.objects.select_related("staff")
    serializer_class = EmployeeProfileSerializer
    filterset_fields = ["staff", "tax_status"]
    search_fields = ["staff__first_name", "staff__last_name", "staff__employee_number", "pan_number"]
    required_permissions = CRUD

    @extend_schema(tags=[TAG], summary="My HR profile", responses={200: EmployeeProfileSerializer})
    @action(detail=False, methods=["get"])
    def me(self, request):
        profile = self.own_rows(request).first()
        if profile is None:
            raise NotFound("HR hasn't set up your profile yet.")
        return Response(EmployeeProfileSerializer(profile).data)


@_schema("staff document")
class StaffDocumentViewSet(StaffRecordViewSet):
    queryset = StaffDocument.objects.select_related("staff")
    serializer_class = StaffDocumentSerializer
    filterset_fields = ["staff", "kind"]
    search_fields = ["title", "number", "staff__first_name", "staff__last_name"]
    required_permissions = CRUD

    def get_queryset(self):
        qs = super().get_queryset()
        within = self.request.query_params.get("expiring_within")
        if within is not None and self.action == "list":
            if not within.isdigit():
                raise ValidationError({"expiring_within": "A number of days."})
            qs = qs.filter(expires_on__isnull=False,
                           expires_on__lte=timezone.localdate() + timedelta(days=int(within)))
        return qs

    @extend_schema(tags=[TAG], summary="My documents on file", responses={200: StaffDocumentSerializer(many=True)})
    @action(detail=False, methods=["get"])
    def me(self, request):
        return Response(StaffDocumentSerializer(self.own_rows(request), many=True).data)


# ---------------------------------------------------------------------------
# Leave setup
# ---------------------------------------------------------------------------
@_schema("fiscal year")
class FiscalYearViewSet(OrganizationScopedViewSet):
    queryset = FiscalYear.objects.all()
    serializer_class = FiscalYearSerializer
    audit_module = "hr"
    search_fields = ["name"]
    required_permissions = CRUD

    def perform_destroy(self, instance):
        if services.fiscal_year_in_use(instance):
            raise ConflictError("Leave has been recorded in this year.", code="in_use")
        super().perform_destroy(instance)


@_schema("leave type")
class LeaveTypeViewSet(OrganizationScopedViewSet):
    """Readable by every member of the organization, so staff can see what
    they can apply for; changed by HR."""

    queryset = LeaveType.objects.all()
    serializer_class = LeaveTypeSerializer
    audit_module = "hr"
    filterset_fields = ["is_active", "is_paid"]
    search_fields = ["code", "name"]
    required_permissions = CRUD

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated(), IsSameOrganization()]
        return super().get_permissions()

    def perform_destroy(self, instance):
        if instance.requests.exists() or instance.balances.exists():
            raise ConflictError("Leave of this type is on record; deactivate it instead.", code="in_use")
        super().perform_destroy(instance)


@_schema("leave balance", "list", "retrieve")
class LeaveBalanceViewSet(StaffRecordViewSet):
    http_method_names = ["get", "post", "head", "options"]
    queryset = LeaveBalance.objects.select_related("staff", "leave_type", "fiscal_year")
    serializer_class = LeaveBalanceSerializer
    filterset_fields = ["staff", "leave_type", "fiscal_year"]
    required_permissions = {**READ, "adjust": [MANAGE], "open": [MANAGE]}

    def create(self, request, *args, **kwargs):
        # Balances are made by ``open`` or on first use, never posted directly; this
        # guards a superuser, who skips HasPermission, from a create() with no fields.
        raise MethodNotAllowed("POST")

    @extend_schema(tags=[TAG], summary="Add or remove days by hand", request=AdjustBalanceSerializer,
                   responses={200: LeaveBalanceSerializer})
    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        balance = self.get_object()
        serializer = AdjustBalanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        balance = services.adjust_balance(balance, by=request.user, **serializer.validated_data)
        return Response(LeaveBalanceSerializer(balance).data)

    @extend_schema(tags=[TAG], summary="Open a fiscal year's balances for every staff member",
                   description="Entitlements (pro-rated for joiners) and carry-forward from the year before. "
                               "Safe to run again: existing balances are left alone.",
                   request=OpenBalancesSerializer, responses={200: None})
    @action(detail=False, methods=["post"])
    def open(self, request):
        serializer = OpenBalancesSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        year, campus = serializer.validated_data["fiscal_year"], serializer.validated_data.get("campus")
        staff = StaffMember.objects.filter(organization_id=year.organization_id)
        campus_ids = services.campus_ids_with_permission(request.user, MANAGE)
        if campus is not None:
            self.check_campus_allowed(campus)
            staff = staff.filter(campus=campus)
        elif campus_ids is not None:
            staff = staff.filter(campus_id__in=campus_ids)
        return Response(services.open_balances(year, staff_members=staff))

    @extend_schema(tags=[TAG], summary="My leave balances this fiscal year",
                   responses={200: LeaveBalanceSerializer(many=True)})
    @action(detail=False, methods=["get"])
    def me(self, request):
        staff = _own_staff(request)
        year = services.fiscal_year_on(staff.organization_id, timezone.localdate())
        if year is not None:
            services.open_balances(year, staff_members=[staff])
        rows = LeaveBalance.objects.filter(staff=staff).select_related("leave_type", "fiscal_year", "staff")
        if year is not None:
            rows = rows.filter(fiscal_year=year)
        return Response(LeaveBalanceSerializer(rows, many=True).data)


# ---------------------------------------------------------------------------
# Leave requests
# ---------------------------------------------------------------------------
@_schema("leave request", "list", "retrieve")
class LeaveRequestViewSet(StaffRecordViewSet):
    """Staff apply through ``me``; HR can apply on someone's behalf with a
    plain POST. Approvers work from ``pending``."""

    http_method_names = ["get", "post", "head", "options"]
    queryset = LeaveRequest.objects.select_related("staff", "leave_type", "fiscal_year")
    serializer_class = LeaveRequestSerializer
    filterset_fields = ["staff", "leave_type", "fiscal_year", "status", "half_day"]
    search_fields = ["staff__first_name", "staff__last_name", "staff__employee_number"]
    ordering_fields = ["start_date", "created_at"]
    required_permissions = {**READ, "create": [MANAGE], "pending": [APPROVE], "approve": [APPROVE],
                            "reject": [APPROVE]}
    self_service_actions = ("me", "cancel")

    @extend_schema(tags=[TAG], summary="Apply for leave on a staff member's behalf",
                   request=CreateLeaveRequestSerializer, responses={201: LeaveRequestSerializer})
    def create(self, request, *args, **kwargs):
        serializer = CreateLeaveRequestSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        self.check_campus_allowed(data["staff"].campus)
        leave = services.apply_leave(by=request.user, **data)
        return Response(LeaveRequestSerializer(leave).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=[TAG], summary="Requests waiting for a decision",
                   responses={200: LeaveRequestSerializer(many=True)})
    @action(detail=False, methods=["get"])
    def pending(self, request):
        qs = self.filter_queryset(self.get_queryset()).filter(status=LeaveStatus.PENDING)
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(LeaveRequestSerializer(page, many=True).data)

    @extend_schema(tags=[TAG], summary="Approve a leave request", request=DecideLeaveSerializer,
                   responses={200: LeaveRequestSerializer})
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        leave = self.get_object()
        serializer = DecideLeaveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        leave = services.approve_leave(leave, by=request.user, **serializer.validated_data)
        return Response(LeaveRequestSerializer(leave).data)

    @extend_schema(tags=[TAG], summary="Reject a leave request", request=RejectLeaveSerializer,
                   responses={200: LeaveRequestSerializer})
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        leave = self.get_object()
        serializer = RejectLeaveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        leave = services.reject_leave(leave, by=request.user, **serializer.validated_data)
        return Response(LeaveRequestSerializer(leave).data)

    @extend_schema(tags=[TAG], summary="Cancel a leave request",
                   description="The applicant, while it's pending or before it starts; HR or an approver "
                               "for the campus, any time. Approved days go back to the balance and to "
                               "attendance.", request=None, responses={200: LeaveRequestSerializer})
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        user = request.user
        leave = LeaveRequest.objects.select_related("staff", "leave_type").filter(pk=pk).first()
        if leave is None or not (getattr(user, "is_platform_admin", False)
                                 or leave.organization_id == user.organization_id):
            raise NotFound()
        is_own = leave.staff.user_id is not None and leave.staff.user_id == user.pk
        campus = leave.staff.campus_id
        if not (is_own or services.holds(user, VIEW, campus) or services.holds(user, APPROVE, campus)):
            raise NotFound()
        leave = services.cancel_leave(leave, by=user)
        return Response(LeaveRequestSerializer(leave).data)

    @extend_schema(methods=["get"], tags=[TAG], summary="My leave requests",
                   parameters=[OpenApiParameter("status", str)], responses={200: LeaveRequestSerializer(many=True)})
    @extend_schema(methods=["post"], tags=[TAG], summary="Apply for leave", request=ApplyLeaveSerializer,
                   responses={201: LeaveRequestSerializer})
    @action(detail=False, methods=["get", "post"])
    def me(self, request):
        staff = _own_staff(request)
        if request.method == "POST":
            serializer = ApplyLeaveSerializer(data=request.data, context=self.get_serializer_context())
            serializer.is_valid(raise_exception=True)
            leave = services.apply_leave(staff=staff, by=request.user, **serializer.validated_data)
            return Response(LeaveRequestSerializer(leave).data, status=status.HTTP_201_CREATED)
        rows = LeaveRequest.objects.filter(staff=staff).select_related("staff", "leave_type", "fiscal_year")
        wanted = request.query_params.get("status")
        if wanted:
            rows = rows.filter(status=wanted)
        return Response(LeaveRequestSerializer(rows, many=True).data)
