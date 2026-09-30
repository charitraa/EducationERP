"""Inventory: consumable stock kept per store, and fixed assets tracked one by one.

An **Item** is a catalog entry — either a *consumable* (chalk, paper, kept as a
quantity) or an *asset* (a projector, a laptop, each unit tracked on its own).
Consumable quantity lives in a **StockLevel** per (item, store), and only ever
changes by appending a **StockMovement** — the level is a running total of the
ledger, never edited directly, the same way ``Invoice.paid_amount`` follows its
payments. A fixed **Asset** is one physical unit with its own tag, assignment
history, maintenance log and, at the end, a disposal record.
"""
from django.db import models
from django.db.models import Q

from core.common.models import OrganizationOwnedModel

ALIVE = Q(deleted_at__isnull=True)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
class ItemCategory(OrganizationOwnedModel):
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=100)

    class Meta:
        db_table = "inventory_category"
        ordering = ["name", "pk"]
        verbose_name_plural = "item categories"
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], condition=ALIVE,
                                    name="uniq_inventory_category_code"),
        ]

    def __str__(self):
        return self.name


class Supplier(OrganizationOwnedModel):
    name = models.CharField(max_length=200)
    contact_person = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    tax_number = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "inventory_supplier"
        ordering = ["name", "pk"]

    def __str__(self):
        return self.name


class ItemKind(models.TextChoices):
    CONSUMABLE = "consumable", "Consumable (kept as a quantity)"
    ASSET = "asset", "Fixed asset (tracked one by one)"


class Item(OrganizationOwnedModel):
    """A catalog entry, shared across the whole organization; where it is and
    how many there are is a question about its stock levels or assets."""

    category = models.ForeignKey(ItemCategory, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="items")
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=12, choices=ItemKind.choices, default=ItemKind.CONSUMABLE, db_index=True)
    unit = models.CharField(max_length=20, default="pcs")
    reorder_level = models.PositiveIntegerField(
        default=0, help_text="Consumables: alert when a store's quantity falls to this or below. 0 = never.")
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "inventory_item"
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], condition=ALIVE, name="uniq_inventory_item_code"),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"


class Store(OrganizationOwnedModel):
    """A place stock is kept at one campus: the main store room, the science
    lab cupboard, the canteen pantry."""

    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="+")
    code = models.SlugField(max_length=30)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "inventory_store"
        ordering = ["campus_id", "code"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "campus", "code"], condition=ALIVE,
                                    name="uniq_inventory_store_code"),
        ]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Stock
# ---------------------------------------------------------------------------
class StockLevel(OrganizationOwnedModel):
    """The running quantity of one consumable in one store. Written only by
    ``services.apply_movement`` under a row lock — never through the API."""

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="levels")
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="levels")
    quantity = models.IntegerField(default=0)

    class Meta:
        db_table = "inventory_stock_level"
        ordering = ["item_id", "store_id"]
        constraints = [
            models.UniqueConstraint(fields=["item", "store"], condition=ALIVE, name="uniq_inventory_level"),
            models.CheckConstraint(condition=Q(quantity__gte=0), name="inventory_level_not_negative"),
        ]

    def __str__(self):
        return f"{self.item} @ {self.store}: {self.quantity}"


class MovementKind(models.TextChoices):
    RECEIPT = "receipt", "Received from a supplier"
    ISSUE = "issue", "Issued out"
    TRANSFER_OUT = "transfer_out", "Transferred out"
    TRANSFER_IN = "transfer_in", "Transferred in"
    ADJUSTMENT = "adjustment", "Adjustment"


class StockMovement(OrganizationOwnedModel):
    """One line of the append-only ledger. ``delta`` is signed; ``balance_after``
    is the store's quantity once it applied. Never edited or deleted — a
    mistake is a new, opposite movement with its own reason."""

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="movements")
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="movements")
    kind = models.CharField(max_length=14, choices=MovementKind.choices, db_index=True)
    delta = models.IntegerField()
    balance_after = models.IntegerField()
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    purchase_line = models.ForeignKey("inventory.PurchaseLine", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="movements")
    stock_issue = models.ForeignKey("inventory.StockIssue", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="movements")
    transfer = models.ForeignKey("inventory.StockTransfer", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="movements")
    created_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        db_table = "inventory_stock_movement"
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["organization", "item", "store"])]
        constraints = [models.CheckConstraint(condition=~Q(delta=0), name="inventory_movement_not_zero")]

    def __str__(self):
        return f"{self.kind} {self.delta:+d} {self.item}"


class StockTransfer(OrganizationOwnedModel):
    """Stock moved from one store to another, possibly at another campus: one
    row here, two movements in the ledger."""

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="transfers")
    from_store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="transfers_out")
    to_store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="transfers_in")
    quantity = models.PositiveIntegerField()
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        db_table = "inventory_stock_transfer"
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=~Q(from_store=models.F("to_store")), name="inventory_transfer_two_stores"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="inventory_transfer_positive"),
        ]

    def __str__(self):
        return f"{self.quantity} x {self.item}: {self.from_store} -> {self.to_store}"


class StockIssue(OrganizationOwnedModel):
    """A voucher handing consumables to a staff member or a department — one
    voucher, one or more lines, one out-movement per line."""

    number = models.CharField(max_length=30)
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="issues")
    staff = models.ForeignKey("staff.StaffMember", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    department = models.ForeignKey("academics.Department", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+")
    purpose = models.CharField(max_length=255, blank=True)
    issued_on = models.DateField()
    issued_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")

    class Meta:
        db_table = "inventory_stock_issue"
        ordering = ["-issued_on", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "number"], condition=ALIVE,
                                    name="uniq_inventory_issue_number"),
            models.CheckConstraint(
                condition=(Q(staff__isnull=False, department__isnull=True)
                           | Q(staff__isnull=True, department__isnull=False)),
                name="inventory_issue_exactly_one_recipient"),
        ]

    def __str__(self):
        return self.number


class StockIssueLine(OrganizationOwnedModel):
    issue = models.ForeignKey(StockIssue, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    quantity = models.PositiveIntegerField()

    class Meta:
        db_table = "inventory_stock_issue_line"
        ordering = ["pk"]
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name="inventory_issue_line_positive")]


# ---------------------------------------------------------------------------
# Purchasing
# ---------------------------------------------------------------------------
class PurchaseStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ORDERED = "ordered", "Ordered"
    PARTIAL = "partial", "Partly received"
    RECEIVED = "received", "Fully received"
    CLOSED = "closed", "Closed short (rest will not arrive)"
    CANCELLED = "cancelled", "Cancelled"


class PurchaseOrder(OrganizationOwnedModel):
    number = models.CharField(max_length=30)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="orders")
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="orders",
                              help_text="Where the goods are delivered; its campus owns the order.")
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=10, choices=PurchaseStatus.choices, default=PurchaseStatus.DRAFT,
                              db_index=True)
    ordered_on = models.DateField(null=True, blank=True)
    expected_on = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    cancelled_reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        db_table = "inventory_purchase_order"
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "number"], condition=ALIVE,
                                    name="uniq_inventory_po_number"),
        ]

    def __str__(self):
        return self.number


class PurchaseLine(OrganizationOwnedModel):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    received_quantity = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "inventory_purchase_line"
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="inventory_po_line_positive"),
            models.CheckConstraint(condition=Q(received_quantity__lte=models.F("quantity")),
                                   name="inventory_po_line_not_over_received"),
        ]

    @property
    def outstanding(self) -> int:
        return self.quantity - self.received_quantity


# ---------------------------------------------------------------------------
# Fixed assets
# ---------------------------------------------------------------------------
class AssetStatus(models.TextChoices):
    IN_STORE = "in_store", "In store"
    ASSIGNED = "assigned", "Assigned"
    MAINTENANCE = "maintenance", "Under maintenance"
    DISPOSED = "disposed", "Disposed"


class AssetCondition(models.TextChoices):
    NEW = "new", "New"
    GOOD = "good", "Good"
    FAIR = "fair", "Fair"
    POOR = "poor", "Poor"


class Asset(OrganizationOwnedModel):
    """One physical unit of an asset-kind item. Its status moves only through
    assign / return / maintenance / dispose — never by editing the field."""

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="assets")
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="assets")
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="+")
    tag = models.CharField(max_length=30)
    serial_number = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=12, choices=AssetStatus.choices, default=AssetStatus.IN_STORE,
                              db_index=True)
    condition = models.CharField(max_length=6, choices=AssetCondition.choices, default=AssetCondition.NEW)
    cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    purchased_on = models.DateField(null=True, blank=True)
    warranty_until = models.DateField(null=True, blank=True)
    purchase_line = models.ForeignKey(PurchaseLine, null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="assets")
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "inventory_asset"
        ordering = ["tag"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "tag"], condition=ALIVE, name="uniq_inventory_asset_tag"),
        ]

    def __str__(self):
        return f"{self.tag} ({self.item.name})"


class AssetAssignment(OrganizationOwnedModel):
    """Who has an asset, from when to when. Exactly one holder: a staff
    member, a student, a room or a department. ``returned_on`` empty means
    it is still theirs; only one such row can exist per asset."""

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="assignments")
    staff = models.ForeignKey("staff.StaffMember", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    student = models.ForeignKey("students.Student", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    room = models.ForeignKey("academics.Room", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    department = models.ForeignKey("academics.Department", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+")
    assigned_on = models.DateField()
    returned_on = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    assigned_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    returned_condition = models.CharField(max_length=6, choices=AssetCondition.choices, blank=True)

    class Meta:
        db_table = "inventory_asset_assignment"
        ordering = ["-assigned_on", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["asset"], condition=ALIVE & Q(returned_on__isnull=True),
                                    name="uniq_active_asset_assignment"),
            models.CheckConstraint(
                condition=(
                    Q(staff__isnull=False, student__isnull=True, room__isnull=True, department__isnull=True)
                    | Q(staff__isnull=True, student__isnull=False, room__isnull=True, department__isnull=True)
                    | Q(staff__isnull=True, student__isnull=True, room__isnull=False, department__isnull=True)
                    | Q(staff__isnull=True, student__isnull=True, room__isnull=True, department__isnull=False)),
                name="inventory_assignment_exactly_one_holder"),
        ]

    @property
    def holder_name(self) -> str:
        for field in ("staff", "student", "room", "department"):
            holder = getattr(self, field)
            if holder is not None:
                return getattr(holder, "full_name", None) or holder.name
        return ""


class MaintenanceKind(models.TextChoices):
    PREVENTIVE = "preventive", "Preventive service"
    REPAIR = "repair", "Repair"
    INSPECTION = "inspection", "Inspection"


class MaintenanceStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class MaintenanceRecord(OrganizationOwnedModel):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="maintenance")
    kind = models.CharField(max_length=12, choices=MaintenanceKind.choices)
    status = models.CharField(max_length=12, choices=MaintenanceStatus.choices, default=MaintenanceStatus.SCHEDULED,
                              db_index=True)
    description = models.CharField(max_length=255)
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                 help_text="The vendor doing the work, if external.")
    scheduled_on = models.DateField(null=True, blank=True)
    started_on = models.DateField(null=True, blank=True)
    completed_on = models.DateField(null=True, blank=True)
    cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    outcome = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        db_table = "inventory_maintenance"
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["asset"], condition=ALIVE & Q(status="in_progress"),
                                    name="uniq_inventory_one_job_in_progress"),
        ]


class DisposalMethod(models.TextChoices):
    SOLD = "sold", "Sold"
    SCRAPPED = "scrapped", "Scrapped"
    DONATED = "donated", "Donated"
    LOST = "lost", "Lost"
    STOLEN = "stolen", "Stolen"


class Disposal(OrganizationOwnedModel):
    """The end of an asset's life. One per asset, never edited — the asset
    stays in the register as ``disposed`` so its history remains."""

    asset = models.OneToOneField(Asset, on_delete=models.PROTECT, related_name="disposal")
    method = models.CharField(max_length=10, choices=DisposalMethod.choices)
    disposed_on = models.DateField()
    proceeds = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    reason = models.CharField(max_length=255)
    recorded_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")

    class Meta:
        db_table = "inventory_disposal"
        ordering = ["-disposed_on", "-pk"]
