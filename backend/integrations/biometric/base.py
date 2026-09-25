"""What every adapter produces, and the one way into the attendance service."""
from dataclasses import dataclass, field
from datetime import datetime

from django.utils import timezone

from modules.attendance.models import Punch, Source
from modules.attendance.services import org_timezone, record_punch


@dataclass
class DevicePunch:
    pin: str
    time: datetime  # naive means the device's (the organization's) local time
    direction: str = Punch.Direction.UNKNOWN
    verify: str = ""


@dataclass
class IngestResult:
    accepted: int = 0
    duplicates: int = 0
    unmapped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"accepted": self.accepted, "duplicates": self.duplicates,
                "unmapped_pins": sorted(set(self.unmapped))}


def ingest(device, punches: list[DevicePunch], source: str = Source.BIOMETRIC) -> IngestResult:
    """Store a device's punches. Safe to repeat: devices resend when they
    don't hear back, and the same punch is recognised and skipped."""
    result = IngestResult()
    tz = org_timezone(device.organization)
    for item in punches:
        moment = item.time if timezone.is_aware(item.time) else item.time.replace(tzinfo=tz)
        punch, created = record_punch(
            organization=device.organization, campus=device.campus, device=device, pin=item.pin,
            punched_at=moment, direction=item.direction, verify=item.verify, source=source,
            dedupe_key=f"device:{device.pk}:{item.pin}:{moment.isoformat()}",
        )
        if not created:
            result.duplicates += 1
            continue
        result.accepted += 1
        if punch.staff_id is None and punch.student_id is None:
            result.unmapped.append(item.pin)
    if punches:
        type(device).objects.filter(pk=device.pk).update(last_seen_at=timezone.now())
    return result
