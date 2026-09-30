"""Three permissions, split by job the way Phase 9 splits ``library.manage``
from ``library.circulate``:

``inventory.view``    see items, stock levels, movements, assets and orders
``inventory.manage``  the office: catalog, suppliers, stores, purchase orders,
                      assets, maintenance and disposal
``inventory.stock``   the storekeeper: receive goods, issue, transfer, adjust

Everything here is internal — nothing is readable "by default" the way notices
or the library catalog are. A staff member sees only the assets currently
assigned to them, through ``assets/me/``.
"""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("inventory.view", "View items, stock, purchase orders and assets"),
        PermissionSpec("inventory.manage", "Manage the catalog, suppliers, purchases, assets and disposals"),
        PermissionSpec("inventory.stock", "Receive, issue, transfer and adjust stock"),
    ]
)

grant_to_system_role("campus-admin", ["inventory.view", "inventory.manage", "inventory.stock"])
