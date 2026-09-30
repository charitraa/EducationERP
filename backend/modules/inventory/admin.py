from django.contrib import admin

from .models import (
    Asset,
    AssetAssignment,
    Disposal,
    Item,
    ItemCategory,
    MaintenanceRecord,
    PurchaseLine,
    PurchaseOrder,
    StockIssue,
    StockIssueLine,
    StockLevel,
    StockMovement,
    StockTransfer,
    Store,
    Supplier,
)

for model in (ItemCategory, Supplier, Item, Store, StockLevel, StockMovement, StockTransfer, StockIssue,
              StockIssueLine, PurchaseOrder, PurchaseLine, Asset, AssetAssignment, MaintenanceRecord, Disposal):
    admin.site.register(model)
