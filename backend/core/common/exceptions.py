from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


class ServiceError(Exception):
    """Base class for business-rule failures raised by service functions.

    Services raise these instead of DRF exceptions so the domain layer stays
    independent of the HTTP layer; this handler translates them.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "The request could not be processed."
    default_code = "service_error"

    def __init__(self, detail=None, code=None):
        self.detail = detail or self.default_detail
        self.code = code or self.default_code
        super().__init__(self.detail)


class ConflictError(ServiceError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The request conflicts with the current state."
    default_code = "conflict"


class PermissionDeniedError(ServiceError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "You do not have permission to perform this action."
    default_code = "permission_denied"


def api_exception_handler(exc, context):
    """Return one consistent error envelope for every failure.

    ``{"error": {"code": ..., "message": ..., "details": ...}}``
    """
    if isinstance(exc, ServiceError):
        return Response(
            {"error": {"code": exc.code, "message": exc.detail, "details": None}},
            status=exc.status_code,
        )

    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(detail=getattr(exc, "message_dict", exc.messages))
    elif isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, PermissionDenied):
        exc = exceptions.PermissionDenied()

    response = drf_exception_handler(exc, context)
    if response is None:
        # Unhandled exception: returning None re-raises it, and Django logs it
        # to `django.request` with the request attached. Logging it here too
        # would write every 500 to the error log twice.
        return None

    code = getattr(exc, "default_code", "error")
    detail = response.data

    if isinstance(detail, dict) and "detail" in detail and len(detail) == 1:
        message, details = str(detail["detail"]), None
    elif isinstance(detail, list):
        message, details = "Invalid input.", {"non_field_errors": detail}
    else:
        message, details = "Invalid input.", detail

    response.data = {"error": {"code": code, "message": message, "details": details}}
    return response
