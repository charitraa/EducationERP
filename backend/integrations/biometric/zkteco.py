"""ZKTeco devices, over their push protocol (ADMS / "iclock").

Set the device's Cloud Server (ADMS) to this server's address. It then:

    GET  /iclock/cdata?SN=…&options=all      asks for its settings (handshake)
    POST /iclock/cdata?SN=…&table=ATTLOG     sends punches, one per line:
         PIN <tab> 2026-09-24 09:58:12 <tab> status <tab> verify <tab> …
    GET  /iclock/getrequest?SN=…             polls for commands (we send none)

The protocol has no credentials: a device is known by its serial number
alone. Only registered, active devices are accepted, and ``allowed_ips``
should pin each to the school's public address.

Times are the device's clock, taken as the organization's time zone.
"""
import logging
from datetime import datetime

from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from modules.attendance.models import AttendanceDevice, Punch

from .auth import ip_allowed
from .base import DevicePunch, ingest

logger = logging.getLogger(__name__)

# ATTLOG status column: what the person pressed (or the device assumed).
DIRECTIONS = {"0": Punch.Direction.IN, "1": Punch.Direction.OUT, "2": Punch.Direction.OUT,
              "3": Punch.Direction.IN, "4": Punch.Direction.IN, "5": Punch.Direction.OUT}
VERIFY = {"0": "password", "1": "fingerprint", "2": "card", "15": "face"}


def _text(body: str, status: int = 200) -> HttpResponse:
    return HttpResponse(body, status=status, content_type="text/plain")


def _device(request):
    serial = (request.GET.get("SN") or "").strip()
    if not serial:
        return None
    device = (AttendanceDevice.objects.filter(serial_number=serial, kind=AttendanceDevice.Kind.ZKTECO,
                                              is_active=True, organization__is_active=True)
              .select_related("organization", "campus").first())
    if device is None or not ip_allowed(device, request):
        logger.warning("Rejected ZKTeco device SN=%s", serial)
        return None
    AttendanceDevice.objects.filter(pk=device.pk).update(last_seen_at=timezone.now())
    return device


def parse_attlog(body: str) -> tuple[list[DevicePunch], int]:
    """Punches from an ATTLOG upload, and how many lines couldn't be read."""
    punches, bad = [], 0
    for line in body.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        try:
            pin = fields[0].strip()
            moment = datetime.strptime(fields[1].strip(), "%Y-%m-%d %H:%M:%S")
        except (IndexError, ValueError):
            bad += 1
            continue
        if not pin:
            bad += 1
            continue
        status = fields[2].strip() if len(fields) > 2 else ""
        verify = fields[3].strip() if len(fields) > 3 else ""
        punches.append(DevicePunch(pin=pin, time=moment,
                                   direction=DIRECTIONS.get(status, Punch.Direction.UNKNOWN),
                                   verify=VERIFY.get(verify, "other" if verify else "")))
    return punches, bad


@csrf_exempt
@require_http_methods(["GET", "POST"])
def cdata(request):
    device = _device(request)
    if device is None:
        return _text("Unknown device", status=401)

    if request.method == "GET":
        serial = device.serial_number
        return _text("\n".join([
            f"GET OPTION FROM: {serial}",
            "ATTLOGStamp=None", "OPERLOGStamp=9999", "ATTPHOTOStamp=None",
            "ErrorDelay=30", "Delay=10", "TransTimes=00:00;14:05", "TransInterval=1",
            "TransFlag=TransData AttLog", "Realtime=1", "Encrypt=None",
        ]) + "\n")

    if request.GET.get("table") != "ATTLOG":
        # User lists, operation logs, photos: acknowledged, not used.
        return _text("OK")
    punches, bad = parse_attlog(request.body.decode("utf-8", errors="replace"))
    if bad:
        logger.warning("ZKTeco SN=%s sent %d unreadable ATTLOG line(s)", device.serial_number, bad)
    ingest(device, punches)
    return _text(f"OK: {len(punches)}")


@csrf_exempt
@require_http_methods(["GET", "POST"])
def getrequest(request):
    if _device(request) is None:
        return _text("Unknown device", status=401)
    return _text("OK")


@csrf_exempt
@require_http_methods(["POST"])
def devicecmd(request):
    if _device(request) is None:
        return _text("Unknown device", status=401)
    return _text("OK")
