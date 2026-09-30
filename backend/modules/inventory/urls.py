from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("inventory/categories", views.ItemCategoryViewSet, basename="inventory-category")
router.register("inventory/suppliers", views.SupplierViewSet, basename="inventory-supplier")
router.register("inventory/items", views.ItemViewSet, basename="inventory-item")
router.register("inventory/stores", views.StoreViewSet, basename="inventory-store")
router.register("inventory/stock-levels", views.StockLevelViewSet, basename="inventory-stock-level")
router.register("inventory/stock-movements", views.StockMovementViewSet, basename="inventory-stock-movement")
router.register("inventory/stock-transfers", views.StockTransferViewSet, basename="inventory-stock-transfer")
router.register("inventory/stock-issues", views.StockIssueViewSet, basename="inventory-stock-issue")
router.register("inventory/purchase-orders", views.PurchaseOrderViewSet, basename="inventory-purchase-order")
router.register("inventory/assets", views.AssetViewSet, basename="inventory-asset")
router.register("inventory/asset-assignments", views.AssetAssignmentViewSet, basename="inventory-asset-assignment")
router.register("inventory/maintenance", views.MaintenanceViewSet, basename="inventory-maintenance")
router.register("inventory/disposals", views.DisposalViewSet, basename="inventory-disposal")

urlpatterns = router.urls
