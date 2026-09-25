"""The generic device API, for any reader or vendor software that can make
an HTTPS request:

    POST /api/v1/attendance/device-punches/
    Authorization: Device <key>
    {"punches": [{"pin": "1001", "time": "2026-09-24T09:58:12", "direction": "in"}]}

Times without an offset are the organization's local time.
"""
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from modules.attendance.models import Punch, Source

from .auth import DeviceAuthentication, DevicePrincipal
from .base import DevicePunch, ingest

MAX_BATCH = 1000


class IsDevice(BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.user, DevicePrincipal)


class DevicePunchItemSerializer(serializers.Serializer):
    pin = serializers.CharField(max_length=32)
    time = serializers.DateTimeField(default_timezone=None)
    direction = serializers.ChoiceField(choices=Punch.Direction.choices, default=Punch.Direction.UNKNOWN)
    verify = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")


class DevicePunchesSerializer(serializers.Serializer):
    punches = DevicePunchItemSerializer(many=True, allow_empty=False, max_length=MAX_BATCH)


class DevicePunchView(APIView):
    authentication_classes = [DeviceAuthentication]
    permission_classes = [IsDevice]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "device"

    @extend_schema(tags=["attendance"], summary="Send punches from a device (device API key)",
                   request=DevicePunchesSerializer, responses={200: None})
    def post(self, request):
        serializer = DevicePunchesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        punches = [DevicePunch(pin=p["pin"].strip(), time=p["time"], direction=p["direction"],
                               verify=p["verify"]) for p in serializer.validated_data["punches"]]
        result = ingest(request.user.device, punches, source=Source.API)
        return Response(result.as_dict())
