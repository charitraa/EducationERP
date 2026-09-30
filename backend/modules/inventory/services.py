"""Stock movements, purchasing, stock issue vouchers and the asset lifecycle.

Views and serializers call these so a rule lives in one place. Every function
that changes stock or an asset runs in one transaction with the rows locked
(``select_for_update``), so two storekeepers can't both take the last box of
chalk, and an asset can't be assigned twice at once.
"""
from django.db import transaction
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, PermissionDeniedError, ServiceError
from core.permissions.selectors import campus_ids_with_permission
from modules.notifications.services import notify

from . import selectors
from .models import (
    Asset,
    AssetAssignment,
    AssetStatus,
    Disposal,
    Item,
    ItemKind,
    MaintenanceRecord,
    MaintenanceStatus,
    MovementKind,
    PurchaseLine,
    PurchaseOrder,
    PurchaseStatus,
    StockIssue,
    StockIssueLine,
    StockLevel,
    StockMovement,
    StockTransfer,
)

MODULE = "inventory"
VIEW = "inventory.view"
MANAGE = "inventory.manage"
STOCK = "inventory.stock"


# ---------------------------------------------------------------------------
# Permissions and numbering
# ---------------------------------------------------------------------------
def holds(user, code: str, campus_id) -> bool:
    if user.is_superuser:
        return True
    campus_ids = campus_ids_with_permission(user, code)
    return campus_ids is None or campus_id in campus_ids


def ensure_holds(user, code: str, campus_id) -> None:
    if not holds(user, code, campus_id):
        raise PermissionDeniedError("Your role does not cover this campus for this action.", code="wrong_campus")


def _next_number(organization_id: int, prefix: str, model) -> str:
    from core.organizations.models import Organization

    with transaction.atomic():
        Organization.objects.select_for_update().get(pk=organization_id)
        count = model.all_objects.filter(organization_id=organization_id).count()
        return f"{prefix}{count + 1:06d}"


def _same_org(organization_id, *rows) -> None:
    for row in rows:
        if row is not None and row.organization_id != organization_id:
            raise ServiceError("That record belongs to another organization.", code="wrong_organization")


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------
def _locked_level(item: Item, store) -> StockLevel:
    level, _ = StockLevel.objects.get_or_create(
        item=item, store=store, defaults={"organization_id": item.organization_id, "quantity": 0})
    return StockLevel.objects.select_for_update().get(pk=level.pk)


def _require_consumable(item: Item) -> None:
    if item.kind != ItemKind.CONSUMABLE:
        raise ServiceError(f"{item.name} is a fixed asset, not a consumable — it has no quantity.",
                           code="not_consumable")


def apply_movement(*, item: Item, store, kind: str, delta: int, by=None, note: str = "", unit_cost=None,
                   purchase_line=None, stock_issue=None, transfer=None, level: StockLevel | None = None):
    """Append one movement and move the store's running quantity with it.

    The only writer of ``StockLevel.quantity``. Call inside a transaction; the
    level row is locked here (or by the caller, when it locks several in a
    fixed order first). Refuses to take a store below zero.
    """
    _require_consumable(item)
    _same_org(item.organization_id, store)
    if delta == 0:
        raise ServiceError("A stock movement can't be zero.", code="zero_movement")
    level = level or _locked_level(item, store)
    before, after = level.quantity, level.quantity + delta
    if after < 0:
        raise ConflictError(f"Only {before} {item.unit} of {item.name} in {store.name}; can't take {-delta}.",
                            code="insufficient_stock", details={"available": before, "requested": -delta})
    level.quantity = after
    level.save(update_fields=["quantity", "updated_at"])
    movement = StockMovement.objects.create(
        organization_id=item.organization_id, item=item, store=store, kind=kind, delta=delta, balance_after=after,
        unit_cost=unit_cost, note=note, purchase_line=purchase_line, stock_issue=stock_issue, transfer=transfer,
        created_by=by)
    if delta < 0 and item.reorder_level and after <= item.reorder_level < before:
        _alert_low_stock(item, store, after)
    return movement


def _alert_low_stock(item: Item, store, quantity: int) -> None:
    recipients = selectors.users_holding([STOCK, MANAGE], campus_id=store.campus_id,
                                         organization_id=item.organization_id)
    notify(recipients, event_type="inventory.low_stock", title=f"Low stock: {item.name}",
           body=f"{store.name} is down to {quantity} {item.unit} (reorder level {item.reorder_level}).",
           data={"item": item.pk, "store": store.pk, "quantity": quantity}, organization_id=item.organization_id)


def adjust_stock(*, item: Item, store, delta: int, reason: str, by=None) -> StockMovement:
    """A stock-take correction, damage or write-off. A reason is mandatory."""
    if not reason.strip():
        raise ServiceError("Say why the stock is being adjusted.", code="reason_required")
    ensure_holds(by, STOCK, store.campus_id) if by is not None else None
    with transaction.atomic():
        movement = apply_movement(item=item, store=store, kind=MovementKind.ADJUSTMENT, delta=delta, by=by,
                                  note=reason.strip())
        log(AuditLog.Action.UPDATE, instance=movement, module=MODULE, actor=by,
            changes={"delta": {"before": None, "after": delta}, "reason": {"before": None, "after": reason.strip()}})
    return movement


def transfer_stock(*, item: Item, from_store, to_store, quantity: int, note: str = "", by=None) -> StockTransfer:
    if from_store.pk == to_store.pk:
        raise ServiceError("Pick two different stores.", code="same_store")
    if quantity <= 0:
        raise ServiceError("Quantity must be positive.", code="bad_quantity")
    _same_org(item.organization_id, from_store, to_store)
    if not to_store.is_active:
        raise ConflictError("The receiving store is inactive.", code="inactive_store")
    if by is not None:
        ensure_holds(by, STOCK, from_store.campus_id)
        ensure_holds(by, STOCK, to_store.campus_id)
    with transaction.atomic():
        # Lock both levels in a fixed order so two opposite transfers can't deadlock.
        _require_consumable(item)
        levels = {s.pk: _locked_level(item, s) for s in sorted((from_store, to_store), key=lambda s: s.pk)}
        transfer = StockTransfer.objects.create(
            organization_id=item.organization_id, item=item, from_store=from_store, to_store=to_store,
            quantity=quantity, note=note, created_by=by)
        apply_movement(item=item, store=from_store, kind=MovementKind.TRANSFER_OUT, delta=-quantity, by=by,
                       note=note, transfer=transfer, level=levels[from_store.pk])
        apply_movement(item=item, store=to_store, kind=MovementKind.TRANSFER_IN, delta=quantity, by=by,
                       note=note, transfer=transfer, level=levels[to_store.pk])
        log(AuditLog.Action.CREATE, instance=transfer, module=MODULE, actor=by)
    return transfer


# ---------------------------------------------------------------------------
# Stock issue vouchers
# ---------------------------------------------------------------------------
def issue_stock(*, store, lines, staff=None, department=None, purpose: str = "", issued_on=None, by=None) -> StockIssue:
    """Hand consumables to a staff member or a department. ``lines`` is a list
    of ``(item, quantity)``; every line comes out of ``store`` or none does."""
    if (staff is None) == (department is None):
        raise ServiceError("Name exactly one recipient: a staff member or a department.", code="bad_recipient")
    if not lines:
        raise ServiceError("Add at least one item.", code="no_lines")
    if len({item.pk for item, _ in lines}) != len(lines):
        raise ServiceError("Each item can appear once on a voucher.", code="duplicate_item")
    _same_org(store.organization_id, staff, department)
    if by is not None:
        ensure_holds(by, STOCK, store.campus_id)
    with transaction.atomic():
        issue = StockIssue.objects.create(
            organization_id=store.organization_id, number=_next_number(store.organization_id, "SI-", StockIssue),
            store=store, staff=staff, department=department, purpose=purpose,
            issued_on=issued_on or timezone.localdate(), issued_by=by)
        for item, quantity in sorted(lines, key=lambda line: line[0].pk):  # fixed lock order
            if quantity <= 0:
                raise ServiceError("Quantities must be positive.", code="bad_quantity")
            _same_org(store.organization_id, item)
            StockIssueLine.objects.create(organization_id=store.organization_id, issue=issue, item=item,
                                          quantity=quantity)
            apply_movement(item=item, store=store, kind=MovementKind.ISSUE, delta=-quantity, by=by,
                           note=purpose or issue.number, stock_issue=issue)
        log(AuditLog.Action.CREATE, instance=issue, module=MODULE, actor=by)
    return issue


# ---------------------------------------------------------------------------
# Purchasing
# ---------------------------------------------------------------------------
def create_purchase(*, supplier, store, lines, expected_on=None, note: str = "", by=None) -> PurchaseOrder:
    """A draft purchase order. ``lines`` is a list of ``(item, quantity, unit_price)``."""
    organization_id = store.organization_id
    _same_org(organization_id, supplier, store)
    if not supplier.is_active:
        raise ConflictError("This supplier is inactive.", code="inactive_supplier")
    if not store.is_active:
        raise ConflictError("This store is inactive.", code="inactive_store")
    if not lines:
        raise ServiceError("Add at least one line.", code="no_lines")
    if len({item.pk for item, _, _ in lines}) != len(lines):
        raise ServiceError("Each item can appear once on an order.", code="duplicate_item")
    if by is not None:
        ensure_holds(by, MANAGE, store.campus_id)
    with transaction.atomic():
        order = PurchaseOrder.objects.create(
            organization_id=organization_id, number=_next_number(organization_id, "PO-", PurchaseOrder),
            supplier=supplier, store=store, campus_id=store.campus_id, expected_on=expected_on, note=note,
            created_by=by)
        for item, quantity, unit_price in lines:
            _same_org(organization_id, item)
            if not item.is_active:
                raise ConflictError(f"{item.name} is inactive.", code="inactive_item")
            if quantity <= 0 or unit_price < 0:
                raise ServiceError("Quantity must be positive and price can't be negative.", code="bad_line")
            PurchaseLine.objects.create(organization_id=organization_id, order=order, item=item,
                                        quantity=quantity, unit_price=unit_price)
        log(AuditLog.Action.CREATE, instance=order, module=MODULE, actor=by)
    return order


def _locked_order(order: PurchaseOrder) -> PurchaseOrder:
    return PurchaseOrder.objects.select_for_update().get(pk=order.pk)


def place_order(order: PurchaseOrder, *, by=None) -> PurchaseOrder:
    with transaction.atomic():
        order = _locked_order(order)
        if order.status != PurchaseStatus.DRAFT:
            raise ConflictError("Only a draft order can be placed.", code="not_draft")
        order.status, order.ordered_on = PurchaseStatus.ORDERED, timezone.localdate()
        order.save(update_fields=["status", "ordered_on", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=order, module=MODULE, actor=by,
            changes={"status": {"before": "draft", "after": "ordered"}})
    return order


def cancel_order(order: PurchaseOrder, reason: str, *, by=None) -> PurchaseOrder:
    """Call off what is still to come. With nothing received the order is
    cancelled; once part has arrived it is closed short instead, so what did
    arrive stays on record and the rest is no longer expected."""
    if not reason.strip():
        raise ServiceError("Say why the order is being cancelled.", code="reason_required")
    with transaction.atomic():
        order = _locked_order(order)
        if order.status not in (PurchaseStatus.DRAFT, PurchaseStatus.ORDERED, PurchaseStatus.PARTIAL):
            raise ConflictError("This order is already finished.", code="not_cancellable")
        before = order.status
        after = PurchaseStatus.CLOSED if before == PurchaseStatus.PARTIAL else PurchaseStatus.CANCELLED
        order.status, order.cancelled_reason = after, reason.strip()
        order.save(update_fields=["status", "cancelled_reason", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=order, module=MODULE, actor=by,
            changes={"status": {"before": before, "after": after}})
    return order


def receive_purchase(order: PurchaseOrder, receipts, *, by=None) -> PurchaseOrder:
    """Book a delivery. ``receipts`` is a list of ``(line, quantity)``; a line
    can be delivered in several goes. Consumables become receipt movements,
    asset-kind items become one asset per unit."""
    if not receipts:
        raise ServiceError("Say what arrived.", code="no_lines")
    if len({line.pk for line, _ in receipts}) != len(receipts):
        raise ServiceError("Each line can appear once per delivery.", code="duplicate_line")
    if by is not None:
        ensure_holds(by, STOCK, order.campus_id)
    with transaction.atomic():
        order = _locked_order(order)
        if order.status not in (PurchaseStatus.ORDERED, PurchaseStatus.PARTIAL):
            raise ConflictError("Only an order that has been placed can receive goods.", code="not_receivable")
        lines = {line.pk: line for line in PurchaseLine.objects.select_for_update().filter(order=order)}
        today = timezone.localdate()
        for line, quantity in sorted(receipts, key=lambda r: r[0].pk):
            current = lines.get(line.pk)
            if current is None:
                raise ServiceError("That line isn't on this order.", code="wrong_line")
            if quantity <= 0:
                raise ServiceError("Quantities must be positive.", code="bad_quantity")
            if quantity > current.outstanding:
                raise ConflictError(f"Only {current.outstanding} of {current.item.name} still expected.",
                                    code="over_receipt", details={"outstanding": current.outstanding})
            if current.item.kind == ItemKind.CONSUMABLE:
                apply_movement(item=current.item, store=order.store, kind=MovementKind.RECEIPT, delta=quantity,
                               by=by, note=order.number, unit_cost=current.unit_price, purchase_line=current)
            else:
                for _ in range(quantity):
                    _new_asset(item=current.item, store=order.store, cost=current.unit_price, purchased_on=today,
                               purchase_line=current, by=by)
            current.received_quantity += quantity
            current.save(update_fields=["received_quantity", "updated_at"])
        done = all(line.outstanding == 0 for line in lines.values())
        order.status = PurchaseStatus.RECEIVED if done else PurchaseStatus.PARTIAL
        order.save(update_fields=["status", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=order, module=MODULE, actor=by,
            changes={"status": {"before": None, "after": order.status}})
    return order


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------
def _new_asset(*, item: Item, store, by=None, **fields) -> Asset:
    if item.kind != ItemKind.ASSET:
        raise ServiceError(f"{item.name} is a consumable; only fixed assets get an asset record.", code="not_asset")
    _same_org(item.organization_id, store)
    asset = Asset.objects.create(
        organization_id=item.organization_id, item=item, store=store, campus_id=store.campus_id,
        tag=_next_number(item.organization_id, "AST-", Asset), **fields)
    log(AuditLog.Action.CREATE, instance=asset, module=MODULE, actor=by)
    return asset


def create_asset(*, item: Item, store, by=None, **fields) -> Asset:
    """An asset that didn't come through a purchase order: a donation or the
    opening register."""
    if not item.is_active:
        raise ConflictError(f"{item.name} is inactive.", code="inactive_item")
    if not store.is_active:
        raise ConflictError("This store is inactive.", code="inactive_store")
    if by is not None:
        ensure_holds(by, MANAGE, store.campus_id)
    with transaction.atomic():
        return _new_asset(item=item, store=store, by=by, **fields)


def move_asset(asset: Asset, *, store, note: str = "", by=None) -> Asset:
    """Shelve an asset in another store, at this campus or another one. Only
    an asset in store moves; one out with a holder comes back first."""
    _same_org(asset.organization_id, store)
    if not store.is_active:
        raise ConflictError("This store is inactive.", code="inactive_store")
    if by is not None:
        ensure_holds(by, MANAGE, asset.campus_id)
        ensure_holds(by, MANAGE, store.campus_id)
    with transaction.atomic():
        asset = _locked_asset(asset)
        if asset.status != AssetStatus.IN_STORE:
            raise ConflictError(f"This asset is {asset.get_status_display().lower()}, so it can't be moved.",
                                code="not_in_store")
        if asset.store_id == store.pk:
            raise ServiceError("The asset is already in that store.", code="same_store")
        before = {"store": asset.store_id, "campus": asset.campus_id}
        asset.store, asset.campus_id = store, store.campus_id
        asset.save(update_fields=["store", "campus", "updated_at"])
        changes = {"store": {"before": before["store"], "after": store.pk},
                   "campus": {"before": before["campus"], "after": store.campus_id}}
        if note:
            changes["note"] = {"before": None, "after": note}
        log(AuditLog.Action.UPDATE, instance=asset, module=MODULE, actor=by, changes=changes)
    return asset


def _locked_asset(asset: Asset) -> Asset:
    return Asset.objects.select_for_update().select_related("item").get(pk=asset.pk)


def assign_asset(asset: Asset, *, staff=None, student=None, room=None, department=None, assigned_on=None,
                 note: str = "", by=None) -> AssetAssignment:
    holders = [h for h in (staff, student, room, department) if h is not None]
    if len(holders) != 1:
        raise ServiceError("Name exactly one holder: a staff member, student, room or department.",
                           code="bad_holder")
    _same_org(asset.organization_id, *holders)
    if room is not None and room.campus_id != asset.campus_id:
        raise ServiceError("That room is at a different campus from the asset.", code="wrong_campus")
    with transaction.atomic():
        asset = _locked_asset(asset)
        if asset.status != AssetStatus.IN_STORE:
            raise ConflictError(f"This asset is {asset.get_status_display().lower()}, so it can't be assigned.",
                                code="not_in_store")
        assignment = AssetAssignment.objects.create(
            organization_id=asset.organization_id, asset=asset, staff=staff, student=student, room=room,
            department=department, assigned_on=assigned_on or timezone.localdate(), note=note, assigned_by=by)
        asset.status = AssetStatus.ASSIGNED
        asset.save(update_fields=["status", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=asset, module=MODULE, actor=by,
            changes={"status": {"before": "in_store", "after": "assigned"}})
    return assignment


def return_asset(asset: Asset, *, condition: str = "", returned_on=None, note: str = "", by=None) -> AssetAssignment:
    with transaction.atomic():
        asset = _locked_asset(asset)
        if asset.status != AssetStatus.ASSIGNED:
            raise ConflictError("This asset isn't assigned to anyone.", code="not_assigned")
        assignment = AssetAssignment.objects.select_for_update().get(asset=asset, returned_on__isnull=True)
        returned_on = returned_on or timezone.localdate()
        if returned_on < assignment.assigned_on:
            raise ServiceError("An asset can't come back before it was handed out.", code="bad_date")
        assignment.returned_on = returned_on
        assignment.returned_condition = condition
        if note:
            assignment.note = f"{assignment.note} | {note}".strip(" |")
        assignment.save(update_fields=["returned_on", "returned_condition", "note", "updated_at"])
        asset.status = AssetStatus.IN_STORE
        fields = ["status", "updated_at"]
        if condition:
            asset.condition = condition
            fields.append("condition")
        asset.save(update_fields=fields)
        log(AuditLog.Action.UPDATE, instance=asset, module=MODULE, actor=by,
            changes={"status": {"before": "assigned", "after": "in_store"}})
    return assignment


# ---- maintenance ----------------------------------------------------------
def schedule_maintenance(*, asset: Asset, kind: str, description: str, supplier=None, scheduled_on=None,
                         by=None) -> MaintenanceRecord:
    _same_org(asset.organization_id, supplier)
    if asset.status == AssetStatus.DISPOSED:
        raise ConflictError("This asset has been disposed of.", code="disposed")
    with transaction.atomic():
        record = MaintenanceRecord.objects.create(
            organization_id=asset.organization_id, asset=asset, kind=kind, description=description,
            supplier=supplier, scheduled_on=scheduled_on, created_by=by)
        log(AuditLog.Action.CREATE, instance=record, module=MODULE, actor=by)
    return record


def start_maintenance(record: MaintenanceRecord, *, by=None) -> MaintenanceRecord:
    with transaction.atomic():
        record = MaintenanceRecord.objects.select_for_update().get(pk=record.pk)
        asset = _locked_asset(record.asset)
        if record.status != MaintenanceStatus.SCHEDULED:
            raise ConflictError("Only a scheduled job can be started.", code="not_scheduled")
        if asset.status == AssetStatus.ASSIGNED:
            raise ConflictError("Take the asset back from its holder first.", code="return_first")
        if asset.status != AssetStatus.IN_STORE:
            raise ConflictError(f"This asset is {asset.get_status_display().lower()}.", code="not_in_store")
        record.status, record.started_on = MaintenanceStatus.IN_PROGRESS, timezone.localdate()
        record.save(update_fields=["status", "started_on", "updated_at"])
        asset.status = AssetStatus.MAINTENANCE
        asset.save(update_fields=["status", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=asset, module=MODULE, actor=by,
            changes={"status": {"before": "in_store", "after": "maintenance"}})
    return record


def complete_maintenance(record: MaintenanceRecord, *, cost=None, outcome: str = "", condition: str = "",
                         completed_on=None, by=None) -> MaintenanceRecord:
    with transaction.atomic():
        record = MaintenanceRecord.objects.select_for_update().get(pk=record.pk)
        asset = _locked_asset(record.asset)
        if record.status != MaintenanceStatus.IN_PROGRESS:
            raise ConflictError("Only a job in progress can be completed.", code="not_in_progress")
        completed_on = completed_on or timezone.localdate()
        if completed_on < record.started_on:
            raise ServiceError("A job can't finish before it started.", code="bad_date")
        record.status, record.completed_on = MaintenanceStatus.COMPLETED, completed_on
        record.cost, record.outcome = cost, outcome
        record.save(update_fields=["status", "completed_on", "cost", "outcome", "updated_at"])
        asset.status = AssetStatus.IN_STORE
        fields = ["status", "updated_at"]
        if condition:
            asset.condition = condition
            fields.append("condition")
        asset.save(update_fields=fields)
        log(AuditLog.Action.UPDATE, instance=asset, module=MODULE, actor=by,
            changes={"status": {"before": "maintenance", "after": "in_store"}})
    return record


def cancel_maintenance(record: MaintenanceRecord, reason: str, *, by=None) -> MaintenanceRecord:
    if not reason.strip():
        raise ServiceError("Say why the job is being cancelled.", code="reason_required")
    with transaction.atomic():
        record = MaintenanceRecord.objects.select_for_update().get(pk=record.pk)
        asset = _locked_asset(record.asset)
        if record.status not in (MaintenanceStatus.SCHEDULED, MaintenanceStatus.IN_PROGRESS):
            raise ConflictError("This job is already finished.", code="already_finished")
        was_running = record.status == MaintenanceStatus.IN_PROGRESS
        record.status, record.outcome = MaintenanceStatus.CANCELLED, reason.strip()
        record.save(update_fields=["status", "outcome", "updated_at"])
        if was_running:
            asset.status = AssetStatus.IN_STORE
            asset.save(update_fields=["status", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=record, module=MODULE, actor=by,
            changes={"status": {"before": None, "after": "cancelled"}})
    return record


# ---- disposal -------------------------------------------------------------
def dispose_asset(asset: Asset, *, method: str, reason: str, disposed_on=None, proceeds=None, by=None) -> Disposal:
    if not reason.strip():
        raise ServiceError("Say why the asset is being disposed of.", code="reason_required")
    with transaction.atomic():
        asset = _locked_asset(asset)
        if asset.status == AssetStatus.DISPOSED:
            raise ConflictError("This asset has already been disposed of.", code="already_disposed")
        if asset.status != AssetStatus.IN_STORE:
            raise ConflictError("Take the asset back from its holder / finish maintenance before disposing of it.",
                                code="not_in_store")
        disposal = Disposal.objects.create(
            organization_id=asset.organization_id, asset=asset, method=method, reason=reason.strip(),
            disposed_on=disposed_on or timezone.localdate(), proceeds=proceeds, recorded_by=by)
        MaintenanceRecord.objects.filter(asset=asset, status=MaintenanceStatus.SCHEDULED).update(
            status=MaintenanceStatus.CANCELLED, outcome="Asset disposed of")
        asset.status = AssetStatus.DISPOSED
        asset.save(update_fields=["status", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=asset, module=MODULE, actor=by,
            changes={"status": {"before": "in_store", "after": "disposed"}})
    return disposal
