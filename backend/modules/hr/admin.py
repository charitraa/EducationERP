from django.contrib import admin

from .models import (
    Contract,
    EmployeeProfile,
    FiscalYear,
    LeaveBalance,
    LeaveRequest,
    LeaveType,
    Position,
    StaffDocument,
)

for model in (Position, Contract, EmployeeProfile, StaffDocument, FiscalYear, LeaveType, LeaveBalance, LeaveRequest):
    admin.site.register(model)
