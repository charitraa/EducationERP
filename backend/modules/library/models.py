"""The library: a catalog of books, physical copies of them at a campus,
and the borrowing lifecycle — issue, return, reservation, fine.

A **Book** is a catalog entry (title, authors, publisher) shared across the
whole organization, the same way a **Copy** — one physical item, at one
campus, on one shelf — is what actually gets borrowed. That split mirrors
the examinations module's exam paper vs. mark sheet: one describes what it is, the other is
the thing in front of you.

**Return** isn't its own model: it's ``Issue.status`` moving to ``returned``,
the same shape as ``SupportTicket``'s or ``Appointment``'s status field —
there's nothing about a return worth a row of its own beyond when and to
whom, both of which fit on the issue.
"""
from decimal import Decimal

from django.db import models
from django.db.models import Q

from core.common.models import OrganizationOwnedModel

ALIVE = Q(deleted_at__isnull=True)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
class Author(OrganizationOwnedModel):
    name = models.CharField(max_length=200)
    bio = models.TextField(blank=True)

    class Meta:
        db_table = "library_author"
        ordering = ["name", "pk"]

    def __str__(self):
        return self.name


class Category(OrganizationOwnedModel):
    """A genre or section: fiction, reference, science, ..."""

    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=100)

    class Meta:
        db_table = "library_category"
        ordering = ["name", "pk"]
        verbose_name_plural = "categories"
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], condition=ALIVE, name="uniq_library_category_code"),
        ]

    def __str__(self):
        return self.name


class Publisher(OrganizationOwnedModel):
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "library_publisher"
        ordering = ["name", "pk"]

    def __str__(self):
        return self.name


class Book(OrganizationOwnedModel):
    """One title in the catalog. How many can actually be borrowed right now
    is a question about its ``Copy`` rows, not this record."""

    title = models.CharField(max_length=300)
    isbn = models.CharField(max_length=20, blank=True)
    authors = models.ManyToManyField(Author, blank=True, related_name="books")
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.PROTECT, related_name="books")
    publisher = models.ForeignKey(Publisher, null=True, blank=True, on_delete=models.SET_NULL, related_name="books")
    edition = models.CharField(max_length=50, blank=True)
    language = models.CharField(max_length=50, default="English")
    published_year = models.PositiveSmallIntegerField(null=True, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "library_book"
        ordering = ["title", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "isbn"], condition=ALIVE & ~Q(isbn=""),
                                    name="uniq_library_book_isbn"),
        ]

    def __str__(self):
        return self.title


class Shelf(OrganizationOwnedModel):
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="shelves")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=100, blank=True)

    class Meta:
        db_table = "library_shelf"
        ordering = ["campus_id", "code"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "campus", "code"], condition=ALIVE,
                                    name="uniq_library_shelf_code"),
        ]

    def __str__(self):
        return self.name or self.code


class CopyStatus(models.TextChoices):
    AVAILABLE = "available", "Available"
    ISSUED = "issued", "Issued"
    RESERVED = "reserved", "Held for a reservation"
    LOST = "lost", "Lost"
    DAMAGED = "damaged", "Damaged"
    WITHDRAWN = "withdrawn", "Withdrawn"


class Copy(OrganizationOwnedModel):
    """One physical item of a ``Book``, at one campus."""

    book = models.ForeignKey(Book, on_delete=models.PROTECT, related_name="copies")
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="+")
    shelf = models.ForeignKey(Shelf, null=True, blank=True, on_delete=models.SET_NULL, related_name="copies")
    accession_number = models.CharField(max_length=30)
    status = models.CharField(max_length=10, choices=CopyStatus.choices, default=CopyStatus.AVAILABLE, db_index=True)
    price = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                help_text="Replacement cost, for a lost-copy fine.")
    acquired_on = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "library_copy"
        ordering = ["book_id", "accession_number"]
        verbose_name_plural = "copies"
        constraints = [
            models.UniqueConstraint(fields=["organization", "accession_number"], condition=ALIVE,
                                    name="uniq_library_copy_accession_number"),
        ]

    def __str__(self):
        return f"{self.book.title} ({self.accession_number})"


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------
class MembershipType(models.TextChoices):
    STUDENT = "student", "Student"
    STAFF = "staff", "Staff"


# Defaults applied when a membership is created; the office may override any
# of them per member (a senior student allowed to keep books longer, say).
MEMBERSHIP_DEFAULTS = {
    MembershipType.STUDENT: {"max_books": 3, "loan_period_days": 14, "daily_fine_rate": Decimal("5.00")},
    MembershipType.STAFF: {"max_books": 5, "loan_period_days": 30, "daily_fine_rate": Decimal("5.00")},
}


class Member(OrganizationOwnedModel):
    """Borrowing rights for one student or one staff member — never a raw
    ``User``, so eligibility and identity always trace back to an existing
    student or staff record, the same way ``TeachingAssignment`` never
    points at a bare user either."""

    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="+")
    student = models.ForeignKey("students.Student", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="library_membership")
    staff = models.ForeignKey("staff.StaffMember", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="library_membership")
    membership_type = models.CharField(max_length=10, choices=MembershipType.choices, db_index=True)
    member_number = models.CharField(max_length=30)
    max_books = models.PositiveSmallIntegerField()
    loan_period_days = models.PositiveSmallIntegerField()
    daily_fine_rate = models.DecimalField(max_digits=6, decimal_places=2)
    is_active = models.BooleanField(default=True, db_index=True)
    joined_on = models.DateField()

    class Meta:
        db_table = "library_member"
        ordering = ["member_number"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "member_number"], condition=ALIVE,
                                    name="uniq_library_member_number"),
            models.UniqueConstraint(fields=["organization", "student"], condition=ALIVE & Q(student__isnull=False),
                                    name="uniq_library_member_student"),
            models.UniqueConstraint(fields=["organization", "staff"], condition=ALIVE & Q(staff__isnull=False),
                                    name="uniq_library_member_staff"),
            models.CheckConstraint(
                condition=(Q(student__isnull=False, staff__isnull=True) | Q(student__isnull=True, staff__isnull=False)),
                name="library_member_exactly_one_profile",
            ),
        ]

    def __str__(self):
        return f"{self.member_number} ({self.full_name})"

    @property
    def full_name(self) -> str:
        return self.student.full_name if self.student_id else self.staff.full_name

    @property
    def user(self):
        return self.student.user if self.student_id else self.staff.user


# ---------------------------------------------------------------------------
# Circulation
# ---------------------------------------------------------------------------
class IssueStatus(models.TextChoices):
    ISSUED = "issued", "Issued"
    RETURNED = "returned", "Returned"
    LOST = "lost", "Reported lost"


class Issue(OrganizationOwnedModel):
    """One loan of a ``Copy`` to a ``Member``."""

    copy = models.ForeignKey(Copy, on_delete=models.PROTECT, related_name="issues")
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="issues")
    issued_at = models.DateTimeField()
    due_at = models.DateTimeField()
    returned_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=IssueStatus.choices, default=IssueStatus.ISSUED, db_index=True)
    issued_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    returned_to = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        db_table = "library_issue"
        ordering = ["-issued_at", "-pk"]
        indexes = [models.Index(fields=["organization", "status", "due_at"])]
        constraints = [
            models.UniqueConstraint(fields=["copy"], condition=ALIVE & Q(status="issued"),
                                    name="uniq_active_issue_per_copy"),
        ]

    def __str__(self):
        return f"{self.copy} -> {self.member}"

    @property
    def is_overdue(self) -> bool:
        from django.utils import timezone

        return self.status == IssueStatus.ISSUED and self.due_at < timezone.now()


class FineCategory(models.TextChoices):
    OVERDUE = "overdue", "Overdue"
    LOST = "lost", "Lost copy"
    DAMAGED = "damaged", "Damaged copy"


class FineStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PAID = "paid", "Paid"
    WAIVED = "waived", "Waived"


class Fine(OrganizationOwnedModel):
    """A charge against a member, raised from one ``Issue`` — never edited
    once paid or waived; a correction is a fresh fine or an explicit waiver,
    matching how ``Payment``/``Refund`` keep the money trail intact."""

    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="fines")
    issue = models.ForeignKey(Issue, on_delete=models.PROTECT, related_name="fines")
    category = models.CharField(max_length=10, choices=FineCategory.choices)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    status = models.CharField(max_length=10, choices=FineStatus.choices, default=FineStatus.PENDING, db_index=True)
    note = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    collected_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    waived_at = models.DateTimeField(null=True, blank=True)
    waived_reason = models.CharField(max_length=255, blank=True)
    waived_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        db_table = "library_fine"
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.member}: {self.amount} ({self.category})"


class ReservationStatus(models.TextChoices):
    PENDING = "pending", "Waiting for a copy"
    READY = "ready", "A copy is being held"
    FULFILLED = "fulfilled", "Collected"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired unclaimed"


class Reservation(OrganizationOwnedModel):
    """A member's place in line for a ``Book`` with no available copy right
    now. Queued first-come, first-served (``reserved_at``); the first
    pending reservation is offered the next copy returned."""

    book = models.ForeignKey(Book, on_delete=models.PROTECT, related_name="reservations")
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="reservations")
    status = models.CharField(max_length=10, choices=ReservationStatus.choices,
                              default=ReservationStatus.PENDING, db_index=True)
    reserved_at = models.DateTimeField(auto_now_add=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, help_text="Held until this, then free for the next.")
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    copy = models.ForeignKey(Copy, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                             help_text="The specific copy held once ready, kept after collection too.")
    cancelled_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "library_reservation"
        ordering = ["reserved_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["book", "member"],
                                    condition=ALIVE & Q(status__in=["pending", "ready"]),
                                    name="uniq_active_reservation"),
        ]

    def __str__(self):
        return f"{self.member} waiting for {self.book}"
