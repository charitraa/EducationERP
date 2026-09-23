from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from core.common.mixins import CampusScopedViewSet

from .models import Admission
from .serializers import AdmissionSerializer, DecisionSerializer, EnrollSerializer
from .services import (
    approve_admission,
    enroll_admission,
    reject_admission,
    withdraw_admission,
)


@extend_schema_view(
    list=extend_schema(tags=["admissions"], summary="List applications"),
    retrieve=extend_schema(tags=["admissions"], summary="Retrieve an application"),
    create=extend_schema(tags=["admissions"], summary="Record an application"),
    update=extend_schema(tags=["admissions"], summary="Replace a pending application"),
    partial_update=extend_schema(tags=["admissions"], summary="Update a pending application"),
    destroy=extend_schema(tags=["admissions"], summary="Soft-delete an application that did not enroll"),
)
class AdmissionViewSet(CampusScopedViewSet):
    """Applications and the decisions on them, scoped by campus."""

    queryset = Admission.objects.select_related("campus")
    serializer_class = AdmissionSerializer
    audit_module = "admissions"
    filterset_fields = ["status", "campus"]
    search_fields = ["application_number", "first_name", "last_name", "phone", "email"]
    ordering_fields = ["applied_on", "application_number", "created_at"]

    required_permissions = {
        "list": ["admissions.view"],
        "retrieve": ["admissions.view"],
        "create": ["admissions.create"],
        "update": ["admissions.update"],
        "partial_update": ["admissions.update"],
        "destroy": ["admissions.delete"],
        "approve": ["admissions.review"],
        "reject": ["admissions.review"],
        "withdraw": ["admissions.update"],
        # Enrolling creates a student, so it needs that right as well.
        "enroll": ["admissions.enroll", "students.create"],
    }

    def perform_destroy(self, instance):
        if instance.status in (Admission.Status.APPROVED, Admission.Status.ENROLLED):
            raise ValidationError(
                f"An {instance.status} application is part of the student's record "
                "and cannot be deleted. Withdraw it instead."
            )
        super().perform_destroy(instance)

    def _decision(self, request, service):
        admission = self.get_object()
        serializer = DecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        admission = service(
            admission=admission, note=serializer.validated_data["note"], by=request.user
        )
        return self._response(admission)

    @extend_schema(tags=["admissions"], summary="Approve an application", request=DecisionSerializer, responses={200: AdmissionSerializer})
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._decision(request, approve_admission)

    @extend_schema(tags=["admissions"], summary="Reject an application (note required)", request=DecisionSerializer, responses={200: AdmissionSerializer})
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._decision(request, reject_admission)

    @extend_schema(tags=["admissions"], summary="Withdraw an application", request=DecisionSerializer, responses={200: AdmissionSerializer})
    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        return self._decision(request, withdraw_admission)

    @extend_schema(
        tags=["admissions"],
        summary="Enroll an approved applicant: creates the student and their guardian",
        request=EnrollSerializer,
        responses={200: AdmissionSerializer},
    )
    @action(detail=True, methods=["post"])
    def enroll(self, request, pk=None):
        admission = self.get_object()
        serializer = EnrollSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        admission = enroll_admission(admission=admission, by=request.user, **serializer.validated_data)
        return self._response(admission)

    def _response(self, admission):
        admission.refresh_from_db()
        return Response(AdmissionSerializer(admission, context=self.get_serializer_context()).data)
