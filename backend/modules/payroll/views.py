from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed, NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.common.exceptions import ConflictError
from core.common.mixins import CampusScopedViewSet, OrganizationScopedViewSet
from modules.hr.selectors import profile_for
from modules.staff.selectors import staff_member_for_user

from . import services
from .models import (
    PayComponent,
    PayrollAdjustment,
    PayrollRun,
    PayrollSettings,
    Payslip,
    PayslipLine,
    RunStatus,
    SalaryStructure,
    SalaryStructureLine,
    StaffSalary,
    StaffSalaryLine,
    TaxScheme,
)
from .serializers import (
    CancelRunSerializer,
    CreateRunSerializer,
    MarkPaidSerializer,
    PayComponentSerializer,
    PayrollAdjustmentSerializer,
    PayrollRunSerializer,
    PayrollSettingsSerializer,
    PayslipListSerializer,
    PayslipSerializer,
    SalaryStructureSerializer,
    SetOvertimeSerializer,
    StaffSalarySerializer,
    TaxSchemeSerializer,
)
from .services import APPROVE, MANAGE, VIEW

TAG = "payroll"
READ = {"list": [VIEW], "retrieve": [VIEW]}
CRUD = {**READ, "create": [MANAGE], "update": [MANAGE], "partial_update": [MANAGE], "destroy": [MANAGE]}


def _schema(noun: str, *actions):
    summaries = {"list": f"List {noun}s", "retrieve": f"Retrieve a {noun}",
                 "create": f"Create a {noun}", "update": f"Replace a {noun}",
                 "partial_update": f"Update a {noun}", "destroy": f"Delete a {noun}"}
    return extend_schema_view(**{a: extend_schema(tags=[TAG], summary=summaries[a])
                                 for a in actions or summaries})


class StaffCampusViewSet(CampusScopedViewSet):
    campus_field = "staff__campus"
    audit_module = "payroll"

    def campus_of(self, validated_data):
        staff = validated_data.get("staff")
        return staff.campus if staff is not None else None


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
@_schema("payroll settings row")
class PayrollSettingsViewSet(OrganizationScopedViewSet):
    """One row per organization; without it the defaults apply."""

    queryset = PayrollSettings.objects.all()
    serializer_class = PayrollSettingsSerializer
    audit_module = "payroll"
    required_permissions = CRUD


@_schema("pay component")
class PayComponentViewSet(OrganizationScopedViewSet):
    queryset = PayComponent.objects.all()
    serializer_class = PayComponentSerializer
    audit_module = "payroll"
    filterset_fields = ["kind", "calculation", "is_active"]
    search_fields = ["code", "name"]
    required_permissions = CRUD

    def perform_destroy(self, instance):
        if (SalaryStructureLine.objects.filter(component=instance).exists()
                or StaffSalaryLine.objects.filter(component=instance).exists()
                or PayslipLine.objects.filter(component=instance).exists()):
            raise ConflictError("Salaries or payslips use this component; deactivate it instead.", code="in_use")
        super().perform_destroy(instance)


@_schema("salary structure")
class SalaryStructureViewSet(OrganizationScopedViewSet):
    queryset = SalaryStructure.objects.prefetch_related("lines__component")
    serializer_class = SalaryStructureSerializer
    audit_module = "payroll"
    filterset_fields = ["is_active"]
    search_fields = ["code", "name"]
    required_permissions = CRUD

    def perform_destroy(self, instance):
        if instance.assignments.exists():
            raise ConflictError("Staff are paid on this structure; deactivate it instead.", code="in_use")
        super().perform_destroy(instance)


@_schema("staff salary", "list", "retrieve", "create", "partial_update", "destroy")
class StaffSalaryViewSet(StaffCampusViewSet):
    """Who is paid on which structure, from when. A new assignment ends the
    one before; one that a payslip used can't be edited or deleted."""

    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    queryset = StaffSalary.objects.select_related("staff", "structure").prefetch_related("lines__component")
    serializer_class = StaffSalarySerializer
    filterset_fields = ["staff", "structure"]
    search_fields = ["staff__first_name", "staff__last_name", "staff__employee_number"]
    required_permissions = CRUD
    service_audits_create = True

    def perform_destroy(self, instance):
        services.remove_salary(instance, by=self.request.user)


@_schema("tax scheme")
class TaxSchemeViewSet(OrganizationScopedViewSet):
    queryset = TaxScheme.objects.select_related("fiscal_year").prefetch_related("slabs")
    serializer_class = TaxSchemeSerializer
    audit_module = "payroll"
    filterset_fields = ["fiscal_year", "tax_status"]
    required_permissions = CRUD

    def perform_destroy(self, instance):
        if Payslip.objects.filter(tax_scheme=instance).exists():
            raise ConflictError("Payslips were taxed with this scheme.", code="in_use")
        super().perform_destroy(instance)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
@_schema("payroll run", "list", "retrieve", "create")
class PayrollRunViewSet(CampusScopedViewSet):
    http_method_names = ["get", "post", "head", "options"]
    queryset = PayrollRun.objects.select_related("campus", "fiscal_year")
    serializer_class = PayrollRunSerializer
    audit_module = "payroll"
    filterset_fields = ["campus", "status", "fiscal_year"]
    search_fields = ["name"]
    ordering_fields = ["period_start", "created_at"]
    required_permissions = {**READ, "create": [MANAGE], "compute": [MANAGE], "approve": [APPROVE],
                            "mark_paid": [MANAGE], "cancel": [MANAGE], "bank_sheet": [VIEW]}

    @extend_schema(tags=[TAG], summary="Open a pay period for a campus", request=CreateRunSerializer,
                   responses={201: PayrollRunSerializer})
    def create(self, request, *args, **kwargs):
        serializer = CreateRunSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        self.check_campus_allowed(serializer.validated_data["campus"])
        run = services.create_run(by=request.user, **serializer.validated_data)
        return Response(PayrollRunSerializer(run).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=[TAG], summary="Work out every payslip (again)",
                   description="From each person's salary, approved leave and attendance. Adjustments and "
                               "overtime set by hand carry over. Only while draft.",
                   request=None, responses={200: None})
    @action(detail=True, methods=["post"])
    def compute(self, request, pk=None):
        run = self.get_object()
        result = services.compute_run(run, by=request.user)
        run.refresh_from_db()
        return Response({**result, "run": PayrollRunSerializer(run).data})

    @extend_schema(tags=[TAG], summary="Approve a computed run, locking its payslips", request=None,
                   responses={200: PayrollRunSerializer})
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        run = services.approve_run(self.get_object(), by=request.user)
        return Response(PayrollRunSerializer(run).data)

    @extend_schema(tags=[TAG], summary="Record that an approved run was paid", request=MarkPaidSerializer,
                   responses={200: PayrollRunSerializer})
    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        run = self.get_object()
        serializer = MarkPaidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        run = services.mark_paid(run, by=request.user, **serializer.validated_data)
        return Response(PayrollRunSerializer(run).data)

    @extend_schema(tags=[TAG], summary="Cancel a draft run", request=CancelRunSerializer,
                   responses={200: PayrollRunSerializer})
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        run = self.get_object()
        serializer = CancelRunSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        run = services.cancel_run(run, by=request.user, **serializer.validated_data)
        return Response(PayrollRunSerializer(run).data)

    @extend_schema(tags=[TAG], summary="Bank transfer list: who gets how much, to which account",
                   responses={200: None})
    @action(detail=True, methods=["get"], url_path="bank-sheet")
    def bank_sheet(self, request, pk=None):
        run = self.get_object()
        rows = []
        for payslip in run.payslips.select_related("staff").order_by("staff__first_name", "pk"):
            profile = profile_for(payslip.staff)
            rows.append({
                "payslip": payslip.number, "staff": payslip.staff_id, "staff_name": payslip.staff.full_name,
                "employee_number": payslip.staff.employee_number,
                "bank_name": profile.bank_name if profile else "",
                "bank_branch": profile.bank_branch if profile else "",
                "account_name": profile.bank_account_name if profile else "",
                "account_number": profile.bank_account_number if profile else "",
                "net_pay": str(payslip.net_pay),
            })
        missing = [r["staff_name"] for r in rows if not r["account_number"]]
        return Response({"run": run.pk, "status": run.status, "rows": rows, "without_bank_account": missing})


@_schema("payslip", "list", "retrieve")
class PayslipViewSet(CampusScopedViewSet):
    http_method_names = ["get", "post", "head", "options"]
    queryset = Payslip.objects.select_related("run", "staff").prefetch_related("lines")
    serializer_class = PayslipSerializer
    campus_field = "run__campus"
    audit_module = "payroll"
    filterset_fields = ["run", "staff"]
    search_fields = ["number", "staff__first_name", "staff__last_name", "staff__employee_number"]
    required_permissions = {**READ, "set_overtime": [MANAGE]}

    def get_permissions(self):
        if self.action == "me":
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_serializer_class(self):
        return PayslipListSerializer if self.action == "list" else PayslipSerializer

    def create(self, request, *args, **kwargs):
        # Payslips come only from computing a run; this guards a superuser, who
        # skips HasPermission, from an empty create() on a read-only serializer.
        raise MethodNotAllowed("POST")

    @extend_schema(tags=[TAG], summary="Set a draft payslip's overtime by hand (null: back to the count)",
                   request=SetOvertimeSerializer, responses={200: PayslipSerializer})
    @action(detail=True, methods=["post"], url_path="set-overtime")
    def set_overtime(self, request, pk=None):
        payslip = self.get_object()
        serializer = SetOvertimeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        minutes = serializer.validated_data["minutes"]
        payslip = services.recompute_payslip(payslip, overtime_minutes=minutes, clear_override=minutes is None,
                                             by=request.user)
        return Response(PayslipSerializer(payslip).data)

    @extend_schema(tags=[TAG], summary="My payslips (approved runs only)", responses={200: PayslipSerializer(many=True)})
    @action(detail=False, methods=["get"])
    def me(self, request):
        staff = staff_member_for_user(request.user)
        if staff is None:
            raise NotFound("No staff record is linked to your account.")
        rows = (Payslip.objects.filter(staff=staff, run__status__in=[RunStatus.APPROVED, RunStatus.PAID])
                .select_related("run", "staff").prefetch_related("lines").order_by("-run__period_start"))
        return Response(PayslipSerializer(rows, many=True).data)


@_schema("payroll adjustment", "list", "retrieve", "create", "destroy")
class PayrollAdjustmentViewSet(StaffCampusViewSet):
    """One-off amounts for someone's next payslip. Withdrawn with DELETE
    until the payslip carrying it is approved."""

    http_method_names = ["get", "post", "delete", "head", "options"]
    queryset = PayrollAdjustment.objects.select_related("staff", "payslip__run")
    serializer_class = PayrollAdjustmentSerializer
    filterset_fields = ["staff", "kind", "payslip", "corrects"]
    required_permissions = {**READ, "create": [MANAGE], "destroy": [MANAGE]}

    def perform_destroy(self, instance):
        services.withdraw_adjustment(instance, by=self.request.user)
