# Phase 10 — Inventory

Consumable stock kept per store, and fixed assets tracked one by one: what
the school buys, where it is, who has it, and how it leaves. Everything
follows the conventions in [`phase-1.md`](phase-1.md).

```
ItemCategory ──► Item (consumable | asset)          Supplier
                   │                                   │
      consumable   │   asset                           ▼
                   │                         PurchaseOrder ──► PurchaseLine
   StockLevel ◄── StockMovement ◄── receipt ──────┘         │
   (item × store)    ▲  ▲  ▲                                 │ receipt
                     │  │  └── StockIssue (voucher, lines)    ▼
                     │  └───── StockTransfer (store → store)  Asset ──► AssetAssignment
                     └──────── adjustment (reason required)    │   ──► MaintenanceRecord
                                                               └──────► Disposal (final)
Store (one campus)
```

Code: `backend/modules/inventory/`. `services.py` holds every rule that
moves stock or changes an asset; `selectors.py` finds who to alert about
low stock and which assets a logged-in user holds.

---

## Item vs. Asset: the catalog and the thing itself

An **Item** is a catalog entry shared across the organization, and its
`kind` decides how it is counted:

- **consumable** (chalk, paper, printer toner) is a quantity. How much a
  store holds lives in a `StockLevel` per (item, store).
- **asset** (a projector, a laptop) is tracked one unit at a time. Each
  unit is an `Asset` with its own tag, serial number, condition and
  history.

This is the same split as Phase 9's Book and Copy. An item's `kind` is
locked once it has stock, assets or purchase-order lines, since
switching would strand them.

## Stock is a ledger, never an edited number

`StockLevel.quantity` is a running total of the append-only
`StockMovement` ledger, the same way `Invoice.paid_amount` follows its
payments. `services.apply_movement` is its only writer. It locks the
level row (`select_for_update`), refuses to go below zero (409
`insufficient_stock`) and records `balance_after` on each movement.
Levels and movements are read-only over the API. A mistake is corrected
by a new adjustment that carries its own reason, never by editing a
movement.

Movements come from four places:

| Source | Endpoint | Movement kind |
|---|---|---|
| A delivery on a purchase order | `purchase-orders/{id}/receive/` | `receipt` (carries the unit cost) |
| An issue voucher to a staff member or department | `stock-issues/` | `issue` |
| A transfer between stores, at any campus | `stock-transfers/` | `transfer_out` + `transfer_in` |
| A stock-take, damage or write-off correction | `stock-levels/adjust/` | `adjustment` (reason required) |

A voucher with several lines is all or nothing: if one line is short,
none of them are issued. A transfer locks both levels in a fixed order,
so two opposite transfers can't deadlock.

**Low stock** is an alert, not a cron job. When any movement takes a store's quantity from above the item's `reorder_level` to at or
below it, everyone with `inventory.stock` or `inventory.manage` at that
campus is sent a notification through Phase 8's `notify()`
(`inventory.low_stock`). It fires once, on the crossing, not on every
later movement. `stock-levels/?low=1` lists what is currently low.

## Purchasing

`draft → ordered → partial → received`, and `cancelled` / `closed`.

- The office (`inventory.manage`) drafts the order (supplier, delivery
  store, lines of item × quantity × unit price) and places it.
- The storekeeper (`inventory.stock`) books deliveries, whole or in
  parts. Receiving more than is still outstanding is refused
  (`over_receipt`) and changes nothing. Consumables become `receipt`
  movements. Asset-kind lines become one `Asset` per unit, tagged
  automatically and costed at the line's unit price.
- **Cancel** with nothing received gives `cancelled`. Once part has
  arrived, the same action gives **`closed`** (closed short): what did
  arrive stays on record, and the rest is no longer expected, so later
  deliveries are refused. A reason is always required.

Order numbers (`PO-000001`), voucher numbers (`SI-000001`) and asset
tags (`AST-000001`) are generated per organization under a lock.

## Assets: status moves only through actions

`in_store → assigned → in_store → maintenance → in_store → … → disposed`

- **Register** (`POST assets/`) covers assets that didn't come through
  an order: donations, or the opening register when a school starts
  using the system.
- **Assign** gives the asset to exactly one holder: a staff member, a
  student, a room (which must be at the asset's campus) or a department.
  `AssetAssignment` keeps the history. The database allows only one open
  assignment per asset, and the asset row is locked as well.
- **Return** closes the assignment, optionally recording the condition
  it came back in. It can't be dated before the asset was handed out.
- **Move** shelves an in-store asset in another store, at this campus or
  another. It needs `inventory.manage` at both campuses, the same rule
  as a stock transfer. The asset's campus follows its store.
- **Maintenance** is scheduled, started (only when the asset is in
  store; an assigned one must be returned first), then completed (with
  cost, outcome and new condition) or cancelled (with a reason). Only
  one job per asset can be in progress.
- **Dispose** (sold / scrapped / donated / lost / stolen, with a
  reason) is final. The asset stays in the register as `disposed` so its
  history remains, and any still-scheduled maintenance is cancelled.

`PATCH assets/{id}/` edits only descriptive fields (serial number,
condition, cost, dates, note). There is no PUT and no DELETE on assets.
Holders see what they have through `GET assets/me/`, which needs no
inventory permission.

## Nothing in use is deleted

DELETE is a soft delete, which never reaches the database's `PROTECT`.
So the views check first: an item, store, supplier or category that
anything still refers to answers 409 `in_use`, and the answer is to
deactivate it (`is_active=false`) instead, as in Phase 6. A store's
campus is locked once it holds stock, assets or orders, since those
rows carry the campus too.

## Permissions

Three codes, split by job the way Phase 9 splits `library.manage` from
`library.circulate`:

| Code | Who | What |
|---|---|---|
| `inventory.view` | anyone who needs to look | items, stock, movements, orders, assets |
| `inventory.manage` | the office | catalog, suppliers, stores, purchase orders, assets, maintenance, disposal |
| `inventory.stock` | the storekeeper | receive deliveries, issue, transfer, adjust |

Nothing is readable by default, unlike notices or the library catalog.
`campus-admin` gets all three. Stores, stock, orders and assets are
campus-scoped: a role granted for one campus sees and acts on that
campus's rows only.

## API surface

```text
GET    /api/v1/inventory/categories/  suppliers/  items/  stores/     CRUD (office); DELETE 409 while in use
GET    /api/v1/inventory/stock-levels/          ?item= ?store= ?low=1
POST   /api/v1/inventory/stock-levels/adjust/                         storekeeper; reason required
GET    /api/v1/inventory/stock-movements/                             read-only ledger
GET    /api/v1/inventory/stock-transfers/       POST moves stock between stores
GET    /api/v1/inventory/stock-issues/          POST issues a voucher (staff or department)
GET    /api/v1/inventory/purchase-orders/       POST drafts an order
POST   /api/v1/inventory/purchase-orders/{id}/place/  cancel/  receive/
GET    /api/v1/inventory/assets/  assets/me/    POST registers; PATCH descriptive fields
POST   /api/v1/inventory/assets/{id}/assign/  return/  move/  dispose/
GET    /api/v1/inventory/asset-assignments/     ?active=1
GET    /api/v1/inventory/maintenance/           POST schedules a job
POST   /api/v1/inventory/maintenance/{id}/start/  complete/  cancel/
GET    /api/v1/inventory/disposals/
```

## Not built (by choice, for now)

- **Supplier bills and payments through Finance.** A purchase order
  records prices, but paying the supplier isn't a Phase 6 transaction.
  Phase 6 handles money coming in from students. Money going out to
  suppliers belongs with accounts payable, which fits beside payroll in
  Phase 11 or a later accounting module.
- **Stock valuation (FIFO / weighted average) and depreciation.**
  Receipts carry their unit cost and assets their purchase cost, so
  both can be computed later from data already stored.
- **Requisitions** (a teacher requesting items before the store issues
  them). Today the storekeeper issues directly.
- **Barcode/QR label printing and scanning.** Asset tags are plain
  generated strings; a scanner would be an `integrations/` adapter
  later, as with Phase 9's accession numbers.
- **Batch/expiry tracking** for consumables such as lab chemicals.
