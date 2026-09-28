from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("library/authors", views.AuthorViewSet, basename="library-author")
router.register("library/categories", views.CategoryViewSet, basename="library-category")
router.register("library/publishers", views.PublisherViewSet, basename="library-publisher")
router.register("library/books", views.BookViewSet, basename="library-book")
router.register("library/shelves", views.ShelfViewSet, basename="library-shelf")
router.register("library/copies", views.CopyViewSet, basename="library-copy")
router.register("library/members", views.MemberViewSet, basename="library-member")
router.register("library/issues", views.IssueViewSet, basename="library-issue")
router.register("library/reservations", views.ReservationViewSet, basename="library-reservation")
router.register("library/fines", views.FineViewSet, basename="library-fine")

urlpatterns = router.urls
