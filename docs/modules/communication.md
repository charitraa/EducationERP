# Communication

Notices, in-app/email/SMS/push notifications, direct messaging and
appointment booking, and support tickets. Everything follows the
conventions in [`identity.md`](identity.md).

```
Business event (any module) ──► notifications.services.notify() ──► Notification (in-app, per user)
                                                                  ├─► integrations/email  (console-log stub)
                                                                  ├─► integrations/sms    (console-log stub)
                                                                  └─► integrations/push   (console-log stub)

Notice (office-published, campus or org-wide, audience-filtered)

MessageThread (staff ⇄ one student/parent) ──► Message
AppointmentSlot (staff publishes) ──► Appointment (booked, approved, cancelled, completed)

SupportTicket (anyone raises) ──► TicketComment
```

Code: `backend/modules/notifications/`, `backend/modules/notices/`,
`backend/modules/communication/` (messaging + appointments),
`backend/modules/support/`, and the delivery stubs under
`backend/integrations/{email,sms,push}/`.

---

## The Notification Service is the one door in

The business-event pipeline is `Business Event ->
Notification Service -> in-app / push / email / SMS`. In practice that means
every other module calls `notifications.services.notify(recipients,
event_type=..., title=..., body=..., data=..., organization_id=...)` the
moment something worth telling someone about happens — it never writes a
`Notification` row itself. `notify()` is written synchronously inside the
caller's transaction, the same way `Invoice.paid_amount` and
`StaffAttendanceDay` are kept in step elsewhere — no Celery, no queue.

Wired in: `finance.record_payment` (`finance.payment_received`),
`examinations.publish_exam`/`publish_plan` (`examinations.result_published`),
`events.register`/`decide_registration`
(`events.registration_confirmed`), and `attendance.submit`
(`attendance.marked_absent`, guardians only, since that's the case they
actually need to hear about — not every present mark).

The one exception is `admissions.services._notify_application_approved`: an
applicant has no `User` account yet at that stage, so it reaches them
directly through the email/SMS adapters instead of going through `notify()`,
whose `Notification` row can only ever address an existing `User`.

Delivery is three console-logging stubs (`integrations/email`,
`integrations/sms`, `integrations/push`), each a single `send()` a real
backend (SES/SendGrid, a gateway, FCM/APNs) drops in behind later — nothing
else should call them directly beyond that one admissions exception.

## Notices: published, audience- and campus-filtered

A **Notice** is draft until `POST /notices/{id}/publish/`; publishing locks
in `published_at` (and optionally `expires_at`). `campus` empty means every
campus — the same nullable-scope precedent `CalendarEvent`/`Event` already
use, so visibility is a null-aware `Q(campus__in=...) | Q(campus__isnull=True)`
filter (`selectors.visible_to`), never a plain `campus__in` (SQL's `IN` never
matches `NULL`). `audience` (all/students/parents/staff) narrows it further.
Reading needs no permission code — every member sees what matches their
audience and campus; `notices.manage` is the office's authoring permission.

## Messaging: staff-started, then either side can reply

A **MessageThread** is 1:1 between one staff member and one student or
parent, and can only be *started* by the staff side
(`services.start_thread`) — a plain create endpoint on `Message` would let
anyone cold-start a conversation with a stranger. Once open, either
participant can reply (`services.send_message`) or close it; a closed
thread reopens automatically on the next message. `communication.publish_slots`
etc. don't gate this — starting a thread only needs a `StaffMember` profile,
replying only needs to be a participant (`services.ensure_participant`).

## Appointments: a slot, booked once, the same shape as a seat plan

**AppointmentSlot** is staff-published availability; **Appointment** is one
booking of it. Booking (`services.book_slot`) takes `select_for_update` on
the slot, the same race-safety `events.register` uses for capacity, and a
`uniq_active_appointment_per_slot` constraint (excluding `cancelled`) means a
freed slot can be rebooked without ever allowing two live bookings at once.
`communication.publish_slots` lets a staff member publish and run their own
slots; `communication.manage_slots` is the office override for any slot at
their campus.

## Support tickets: anyone raises, the office (or the assignee) runs

Any student, parent, staff member or teacher can raise a **SupportTicket**;
its lifecycle is `open -> in_progress -> resolved -> closed`
(`services.py`). `campus` follows the raiser when one can be resolved — a
student's or staff member's own, or the first of a parent's linked
children's — and is left empty otherwise (an org-wide issue, or nobody
resolvable), matching the nullable-campus precedent `Event`/`Notice` set.
Anyone who can see a ticket (raised it, assigned to it, or holds
`support.manage`) can add a **TicketComment**; only `support.manage` (or
whoever raised it, for closing) can assign, resolve or close one.
`SupportTicketViewSet` has no PUT/PATCH/DELETE — the workflow only ever
moves through `assign`/`resolve`/`close`, so a bare PATCH can't let a raiser
edit status or assignment directly (the same superuser-bypasses-`HasPermission`
gap the admit-card, invoice and event-registration viewsets guard
against, closed here with `http_method_names` instead of a `create()`
override).

## API surface

```text
GET    /api/v1/notifications/                                        list mine, across every org
POST   /api/v1/notifications/{id}/mark-read/
POST   /api/v1/notifications/mark-all-read/
GET    /api/v1/notifications/unread-count/

GET    /api/v1/notices/                                               CRUD; publish/read scoped by audience+campus
POST   /api/v1/notices/{id}/publish/

GET    /api/v1/communication/threads/                                 mine, as staff or the other side
POST   /api/v1/communication/threads/                                 staff only; starts or reopens
GET    /api/v1/communication/threads/{id}/messages/  POST
POST   /api/v1/communication/threads/{id}/close/

GET    /api/v1/communication/appointment-slots/                       CRUD (staff/office); read open to anyone in the org
POST   /api/v1/communication/appointment-slots/{id}/cancel/
GET    /api/v1/communication/appointments/                            mine, or my slot's, or my campus's
POST   /api/v1/communication/appointments/                            books an open slot
POST   /api/v1/communication/appointments/{id}/approve/  cancel/  complete/

GET    /api/v1/support/tickets/                                       CRUD (create only; raiser/assignee/office read)
POST   /api/v1/support/tickets/{id}/assign/  resolve/  close/
GET    /api/v1/support/tickets/{id}/comments/  POST
```

## Not built (by choice, for now)

- **Real delivery backends.** Email/SMS/push are console-logging stubs;
  swapping in SES/SendGrid, an SMS gateway, and FCM/APNs is a later,
  ops-driven decision, not a schema one.
- **Per-user notification preferences** (mute an event type, digest instead
  of instant). Every matching event notifies right now.
- **A public event/notice feed** beyond what a signed-in member already
  sees. No anonymous or cross-organization access.
- **Group/broadcast messaging.** `MessageThread` is strictly 1:1.
- **SLAs or auto-escalation on support tickets.** Assignment and status
  changes are manual; nothing times one out.
