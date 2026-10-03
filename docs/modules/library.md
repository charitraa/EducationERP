# Library

A catalog of books, the physical copies of them at each campus, and the
borrowing lifecycle: issue, return, reservation, fine. Everything follows
the conventions in [`identity.md`](identity.md).

```
Author ──┐
Category ─┼──► Book ──► Copy (one physical item, at one campus, on a Shelf)
Publisher ┘             │
                         ▼
Member (a Student or a StaffMember's borrowing rights)
   │
   ├──► Issue (issued → returned | lost)  ──► Fine (overdue | lost | damaged)
   │
   └──► Reservation (pending → ready → fulfilled | cancelled | expired)
```

Code: `backend/modules/library/`. `services.py` runs membership,
circulation and reservations and raises fines; `selectors.py` finds a
member from a logged-in user and lists overdue issues.

---

## Book vs. Copy: the catalog and the shelf

A **Book** is a catalog entry — title, authors, publisher, category —
shared across the whole organization. A **Copy** is one physical item of
it, at one campus, on one **Shelf**: the same split as the exam
paper (`ExamSubject`) vs. its mark sheet, one describing what it is, the
other the thing actually in front of you. Browsing the catalog and
checking a copy's availability needs no permission code — every
organization member can, the same way notices and events are readable by
default.

## Return isn't its own model

The build list names "Issue, Return" as two things; here they're one.
`Issue.status` moves `issued → returned` (or `lost`), the same shape as
`SupportTicket`'s or `Appointment`'s status field. A return has nothing
worth a row of its own beyond when it happened and to whom, and both fit
on the issue that's being closed.

## Membership: never a raw user

A **Member** wraps a **Student** or a **StaffMember** — never a bare
`User` — so borrowing rights always trace back to an existing profile, the
same way `TeachingAssignment` never points at a raw user either. Creating
one (`services.create_member`) sets sensible defaults by type
(students: 3 books, 14 days, staff: 5 books, 30 days) that the office may
still override per member, the way a scholarship is a standing override
rather than a schema change.

## Circulation: two permissions, like examinations' manage/mark split

`library.manage` runs the catalog and memberships; `library.circulate` is
the front-desk job — issue, return, fines, reservations. An institution
whose librarian only ever works the desk can be granted just the one
permission through a custom role, without touching the catalog.

Issuing (`POST /library/issues/`) checks, in order: the membership is
active, the copy is at the member's own campus (no borrowing from another
campus's library), and the member is under their book limit
(`services.issue_book`). The copy is locked (`select_for_update`) the same
way `communication.book_slot` protects a slot, so two simultaneous issues
of the last copy can't both succeed.

## Nothing in use is deleted

DELETE is a soft delete, which never reaches the database's `PROTECT`, so
the views check first (409 `in_use`), as inventory does: an
author, category or publisher that a book uses, a book with copies or
reservations, a shelf with copies on it, and a copy that has ever been lent
or held. A copy leaving the library is **withdrawn** instead, which keeps its
history. A copy's shelf must be at the copy's campus, and a shelf can't move
campus while copies are on it. Accession and member numbers count deleted
rows too, so a number is never issued twice.

## Fines: raised from an issue, never edited once settled

A **Fine** always traces back to one `Issue` — overdue (rate × days late),
or a copy's replacement cost, reported damaged or lost on return. Once
`paid` or `waived` it's never edited; a mistake needs an office decision
recorded through the same actions, not a rewritten row, matching how
`Payment`/`Refund` keep the money trail intact elsewhere.

## Reservations: a queue, never a cron

Reserving (`POST /library/reservations/`) is refused outright while a copy
is actually available — there's nothing to queue for. Once the book is
fully checked out, a member joins the line (oldest reservation first); the
next `return` (or a cancelled reservation ahead of them) holds that
specific copy for them for 3 days (`ReservationReady`, notified through
the notification service) rather than releasing it to just anyone.
The desk hands it over with `POST /library/reservations/{id}/fulfil/`,
which issues that exact copy — the ordinary "must be available" issuing
rule doesn't apply to a copy already held for this reservation.
Sweeping past-due holds back to `available` is an explicit
`POST /library/reservations/expire-stale/`, never a cron — the same stance
finance takes with `assess-late-fees`.

## API surface

```text
GET    /api/v1/library/authors/  categories/  publishers/               CRUD (office); read open to all
GET    /api/v1/library/books/                                            CRUD (office); read open to all
GET    /api/v1/library/shelves/                                          CRUD (office only)
GET    /api/v1/library/copies/                                           CRUD (office); read open to all
POST   /api/v1/library/copies/{id}/withdraw/
GET    /api/v1/library/members/  members/me/                             CRUD (office); me is self-service
POST   /api/v1/library/members/{id}/deactivate/
GET    /api/v1/library/issues/  issues/me/                               list/retrieve; POST issues one (desk)
POST   /api/v1/library/issues/{id}/return/                                outcome: returned | damaged | lost
GET    /api/v1/library/reservations/  reservations/me/                   list/retrieve; POST reserves (self or desk)
POST   /api/v1/library/reservations/{id}/cancel/  fulfil/
POST   /api/v1/library/reservations/expire-stale/
GET    /api/v1/library/fines/  fines/me/                                 list/retrieve; POST {id}/pay/  waive/
```

## Not built (by choice, for now)

- **Real fine collection through Finance.** Paying a library fine settles
  it inside this module; it doesn't create a finance `Invoice`/`Payment`.
  Wiring the two together, if wanted, is a later decision — this module
  doesn't assume it's the only way a fine ever gets paid.
- **A waitlist position shown to the member**, beyond knowing they're
  queued. `reservations/me/` shows status, not "you're 3rd in line."
- **Digital copies / e-books.** Physical circulation only.
- **Barcode/RFID scanning at the desk.** `Copy.accession_number` is a
  plain generated string; a real scanner integration is an `integrations/`
  adapter for later, the same way biometric attendance devices arrived
  after the plain API did in attendance.
