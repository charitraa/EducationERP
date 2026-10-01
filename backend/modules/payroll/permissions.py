"""Three permissions. Salaries are sensitive, so no shipped role below
``org-admin`` gets any of them; an organization grants them through its own
role (an "Accountant", say), organization-wide or for one campus.

``payroll.view``     see salary structures, assignments, runs and payslips
``payroll.manage``   set up components, structures, tax schemes and salaries;
                     create and compute runs, adjust draft payslips, mark paid
``payroll.approve``  approve a computed run, which locks its payslips

A staff member sees their own approved payslips through
``/payroll/payslips/me/`` without any of these.
"""
from core.permissions.registry import PermissionSpec, register_permissions

register_permissions(
    [
        PermissionSpec("payroll.view", "View salary structures, staff salaries, payroll runs and payslips"),
        PermissionSpec("payroll.manage", "Set up salaries and tax; create, compute and pay payroll runs"),
        PermissionSpec("payroll.approve", "Approve payroll runs, locking their payslips"),
    ]
)
