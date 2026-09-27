"""Permissions this module declares, and what the shipped roles get.

``finance.collect`` is the cashier: record payments and print receipts, but
not touch fee structures or issue refunds. ``finance.manage`` is the finance
office: fee structures, scholarships, invoice generation, cancelling,
adjustments and refunds.
"""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("finance.view", "View fee structures, invoices, payments and statements"),
        PermissionSpec("finance.collect", "Record payments and issue receipts"),
        PermissionSpec("finance.manage", "Set up fees and scholarships; generate, cancel and adjust invoices; refund"),
    ]
)

grant_to_system_role("campus-admin", ["finance.view", "finance.collect", "finance.manage"])
