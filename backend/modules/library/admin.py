from django.contrib import admin

from .models import Author, Book, Category, Copy, Fine, Issue, Member, Publisher, Reservation, Shelf


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ["name", "organization"]
    search_fields = ["name"]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "organization"]


@admin.register(Publisher)
class PublisherAdmin(admin.ModelAdmin):
    list_display = ["name", "organization"]


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ["title", "isbn", "category", "publisher", "organization"]
    list_filter = ["organization", "category"]
    search_fields = ["title", "isbn"]


@admin.register(Shelf)
class ShelfAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "campus", "organization"]
    list_filter = ["organization", "campus"]


@admin.register(Copy)
class CopyAdmin(admin.ModelAdmin):
    list_display = ["accession_number", "book", "campus", "shelf", "status"]
    list_filter = ["organization", "campus", "status"]
    search_fields = ["accession_number"]


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ["member_number", "membership_type", "campus", "is_active"]
    list_filter = ["organization", "campus", "membership_type", "is_active"]
    search_fields = ["member_number"]


@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin):
    list_display = ["copy", "member", "issued_at", "due_at", "returned_at", "status"]
    list_filter = ["organization", "status"]


@admin.register(Fine)
class FineAdmin(admin.ModelAdmin):
    list_display = ["member", "issue", "category", "amount", "status"]
    list_filter = ["organization", "status", "category"]


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ["book", "member", "status", "reserved_at", "expires_at"]
    list_filter = ["organization", "status"]
