"""Read-side queries other modules use: payroll asks what leave was taken."""
from decimal import Decimal

from modules.attendance.selectors import staff_working_days

from .models import EmployeeProfile, LeaveRequest, LeaveStatus

ONE = Decimal("1")
HALF = Decimal("0.5")


def approved_leave_by_day(staff, start, end) -> dict:
    """Approved leave on ``staff``'s working days in [start, end]:
    ``{date: {"days": 1 or 0.5, "paid": bool, "leave_type": name}}``."""
    result = {}
    requests = (LeaveRequest.objects.filter(staff=staff, status=LeaveStatus.APPROVED, start_date__lte=end,
                                            end_date__gte=start).select_related("leave_type"))
    for request in requests:
        days = staff_working_days(staff, max(start, request.start_date), min(end, request.end_date))
        for day in days:
            result[day] = {"days": HALF if request.half_day else ONE, "paid": request.leave_type.is_paid,
                           "leave_type": request.leave_type.name}
    return result


def profile_for(staff) -> EmployeeProfile | None:
    return EmployeeProfile.objects.filter(staff=staff).first()
