from rest_framework import serializers

from core.common.serializers import ensure_unique_in_organization, ensure_unique_together, target_organization_id

from .models import Author, Book, Category, Copy, Fine, Issue, Member, Publisher, Reservation, Shelf


class OwnedSerializer(serializers.ModelSerializer):
    def own(self, value, label):
        if value is not None and value.organization_id != target_organization_id(self):
            raise serializers.ValidationError(f"Unknown {label}.")
        return value


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "organization", "name", "bio", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "organization", "code", "name", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_code(self, value):
        ensure_unique_in_organization(self, "code", value)
        return value


class PublisherSerializer(serializers.ModelSerializer):
    class Meta:
        model = Publisher
        fields = ["id", "organization", "name", "address", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]


class BookSerializer(OwnedSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)
    publisher_name = serializers.CharField(source="publisher.name", read_only=True, default=None)
    author_names = serializers.SerializerMethodField()
    available_count = serializers.SerializerMethodField()

    class Meta:
        model = Book
        fields = ["id", "organization", "title", "isbn", "authors", "author_names", "category", "category_name",
                  "publisher", "publisher_name", "edition", "language", "published_year", "description",
                  "available_count", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def get_author_names(self, obj) -> list[str]:
        return [a.name for a in obj.authors.all()]

    def get_available_count(self, obj) -> int:
        return obj.copies.filter(status="available").count()

    def validate_category(self, value):
        return self.own(value, "category")

    def validate_publisher(self, value):
        return self.own(value, "publisher")

    def validate_authors(self, value):
        for author in value:
            self.own(author, "author")
        return value

    def validate_isbn(self, value):
        ensure_unique_in_organization(self, "isbn", value)
        return value


class ShelfSerializer(OwnedSerializer):
    class Meta:
        model = Shelf
        fields = ["id", "organization", "campus", "code", "name", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_campus(self, value):
        return self.own(value, "campus")

    def validate(self, attrs):
        ensure_unique_together(self, attrs, ["campus", "code"], "This shelf code is already used at this campus.",
                              extra={"organization_id": target_organization_id(self)})
        campus = attrs.get("campus")
        if self.instance is not None and campus is not None and campus != self.instance.campus \
                and self.instance.copies.exists():
            raise serializers.ValidationError({"campus": "Copies are on this shelf; move them first."})
        return attrs


class CopySerializer(OwnedSerializer):
    book_title = serializers.CharField(source="book.title", read_only=True)
    shelf_name = serializers.CharField(source="shelf.name", read_only=True, default=None)

    class Meta:
        model = Copy
        fields = ["id", "organization", "book", "book_title", "campus", "shelf", "shelf_name",
                  "accession_number", "status", "price", "acquired_on", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "accession_number", "status", "created_at", "updated_at"]

    def validate_book(self, value):
        return self.own(value, "book")

    def validate_campus(self, value):
        return self.own(value, "campus")

    def validate_shelf(self, value):
        if value is not None and value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown shelf.")
        return value

    def validate(self, attrs):
        campus = attrs.get("campus", getattr(self.instance, "campus", None))
        shelf = attrs.get("shelf", getattr(self.instance, "shelf", None))
        if shelf is not None and campus is not None and shelf.campus_id != campus.pk:
            raise serializers.ValidationError({"shelf": "This shelf is at another campus."})
        return attrs


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------
class MemberSerializer(OwnedSerializer):
    full_name = serializers.CharField(read_only=True)
    campus_name = serializers.CharField(source="campus.name", read_only=True)
    active_issues = serializers.SerializerMethodField()
    max_books = serializers.IntegerField(required=False, help_text="Default: the usual limit for the type.")
    loan_period_days = serializers.IntegerField(required=False, help_text="Default: the usual period for the type.")
    daily_fine_rate = serializers.DecimalField(max_digits=6, decimal_places=2, required=False,
                                               help_text="Default: the usual rate for the type.")
    joined_on = serializers.DateField(required=False, help_text="Default: today.")

    class Meta:
        model = Member
        fields = ["id", "organization", "campus", "campus_name", "student", "staff", "membership_type",
                  "member_number", "full_name", "max_books", "loan_period_days", "daily_fine_rate", "is_active",
                  "joined_on", "active_issues", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "membership_type", "member_number", "is_active",
                           "created_at", "updated_at"]

    def get_active_issues(self, obj) -> int:
        return obj.issues.filter(status="issued").count()

    def validate_campus(self, value):
        return self.own(value, "campus")

    def validate_student(self, value):
        return self.own(value, "student")

    def validate_staff(self, value):
        return self.own(value, "staff")


class DeactivateMemberSerializer(serializers.Serializer):
    pass


# ---------------------------------------------------------------------------
# Circulation
# ---------------------------------------------------------------------------
class IssueSerializer(serializers.ModelSerializer):
    book_title = serializers.CharField(source="copy.book.title", read_only=True)
    accession_number = serializers.CharField(source="copy.accession_number", read_only=True)
    member_name = serializers.CharField(source="member.full_name", read_only=True)
    member_number = serializers.CharField(source="member.member_number", read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = Issue
        fields = ["id", "organization", "copy", "book_title", "accession_number", "member", "member_name",
                  "member_number", "issued_at", "due_at", "returned_at", "status", "is_overdue", "issued_by",
                  "returned_to", "created_at"]
        read_only_fields = fields


class IssueBookSerializer(OwnedSerializer):
    copy = serializers.PrimaryKeyRelatedField(queryset=Copy.objects.all())
    member = serializers.PrimaryKeyRelatedField(queryset=Member.objects.all())

    class Meta:
        model = Issue
        fields = ["copy", "member"]

    def validate_copy(self, value):
        return self.own(value, "copy")

    def validate_member(self, value):
        return self.own(value, "member")


class ReturnBookSerializer(serializers.Serializer):
    outcome = serializers.ChoiceField(choices=["returned", "damaged", "lost"], default="returned")


# ---------------------------------------------------------------------------
# Reservations
# ---------------------------------------------------------------------------
class ReservationSerializer(serializers.ModelSerializer):
    book_title = serializers.CharField(source="book.title", read_only=True)
    member_name = serializers.CharField(source="member.full_name", read_only=True)
    accession_number = serializers.CharField(source="copy.accession_number", read_only=True, default=None)

    class Meta:
        model = Reservation
        fields = ["id", "organization", "book", "book_title", "member", "member_name", "status", "reserved_at",
                  "ready_at", "expires_at", "fulfilled_at", "copy", "accession_number", "cancelled_reason",
                  "created_at"]
        read_only_fields = fields


class ReserveBookSerializer(OwnedSerializer):
    book = serializers.PrimaryKeyRelatedField(queryset=Book.objects.all())
    member = serializers.PrimaryKeyRelatedField(queryset=Member.objects.all())

    class Meta:
        model = Reservation
        fields = ["book", "member"]

    def validate_book(self, value):
        return self.own(value, "book")

    def validate_member(self, value):
        return self.own(value, "member")


class CancelReservationSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


# ---------------------------------------------------------------------------
# Fines
# ---------------------------------------------------------------------------
class FineSerializer(serializers.ModelSerializer):
    member_name = serializers.CharField(source="member.full_name", read_only=True)
    member_number = serializers.CharField(source="member.member_number", read_only=True)
    book_title = serializers.CharField(source="issue.copy.book.title", read_only=True)

    class Meta:
        model = Fine
        fields = ["id", "organization", "member", "member_name", "member_number", "issue", "book_title",
                  "category", "amount", "status", "note", "paid_at", "collected_by", "waived_at",
                  "waived_reason", "waived_by", "created_at"]
        read_only_fields = fields


class WaiveFineSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)
