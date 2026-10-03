# Hostel and Transport

Who sleeps where, who rides which bus, and what each costs per term. Two
modules, following the conventions in [`identity.md`](identity.md):
`backend/modules/hostel/` and `backend/modules/transport/`. Both bill
through finance and never write its tables.

```
Building (campus, gender) ──► Floor ──► HostelRoom ──► Bed
                                            │             │
                                       RoomType (fee/term, fee category)
                                                          │
                     Allocation (student | staff, start → end; reserved → checked in → checked out)
Building/HostelRoom ◄── Complaint (raised by a resident or the office; open → in progress → resolved | rejected)

Vehicle (campus, seats) ◄── VehicleDocument (bluebook, insurance, permit… expiry)
   │   ◄── Maintenance, FuelLog
Driver (StaffMember; driver | assistant, licence)
Route (campus, vehicle, driver, assistant, fee/term, fee category) ──► Stop (sequence, times, own fee?)
   │
Assignment (student | staff, stop, both | pickup | drop, start → end)
Trip (route, date, pickup | drop; vehicle and crew copied) ──► TripRecord (assignment: boarded | absent)

finance.Invoice.source = fees | hostel | transport
```

## Decisions (agreed before building)

| Question | Choice |
|---|---|
| Fees | Their **own invoice per term** (`Invoice.source` = `hostel` / `transport`), billed by an idempotent "generate invoices" action through finance's service. Tuition invoices are untouched. |
| Who | **Students and staff** can hold a bed or ride. Only students are billed; staff charges, if any, go through payroll adjustments. |
| Bus roll | **Per trip, per student**: a trip is a route on a date in one direction. The crew marks each rider boarded or absent; parents are told about an absence. Separate from class attendance. |
| Complaints, maintenance | **Own small models**, not support tickets or inventory assets. They need rooms, odometers, fuel and paper expiries, which those don't have. |

## Hostel

- **Buildings** belong to a campus and say who may live there: boys, girls
  or anyone (`gender`, checked against the student's or staff member's
  recorded gender). A **room** sits on a **floor** of its building and has
  a **room type**, which carries the per-bed, per-term fee and the fee
  category the invoice line uses. Capacity is the number of **beds** in
  service; `?available=true` lists free ones.
- **Allocation** (`allocate_hostel_room`) reserves one bed for one person
  from a date. Check-in is on or after that date; check-out records the
  last day, which can't be in the future. A reservation not yet checked
  into can be cancelled with a reason. A **room change** (`move`) ends the
  current stay the day before and opens a new, checked-in one, both or
  neither.
- **One bed, one holder.** The bed row is locked while allocating, and
  partial unique constraints back it: a bed has at most one reserved or
  checked-in allocation, and so does a person. Two wardens giving away the
  last bed at once get one success and one `409 bed_taken`; the live run
  checked this with real concurrent requests.
- **Allocations are history.** No edit, no delete. A bed, room, floor,
  building or room type that has ever been used answers `409 in_use` to a
  delete. Deactivate it instead, since a soft delete would bypass the
  database's PROTECT.
- **Complaints.** A checked-in resident files one with
  `POST complaints/me/`. The building and room come from their bed, and the
  building's warden is notified. The office can also record one. Assigning
  it to a staff member moves it to *in progress*; it ends *resolved* (with
  what was done) or *rejected* (with why). The resident is notified each
  time and lists their own with `GET complaints/me/`.

## Transport

- **Vehicles** belong to a campus and have a seat count. Registration
  numbers are stored upper-case and unique per organization. Their
  **papers** (bluebook, insurance, route permit, pollution, fitness, road
  tax) carry expiry dates; `?expiring_within=30` lists those due, including
  ones already expired. Drivers' licences have the same filter.
- **Drivers** are staff members on the crew list as *driver* or
  *assistant*. A route's driver must be a driver; an assistant can't be
  promoted to the wheel of a route without a role change, and a driver can't
  be demoted while driving one.
- A **route** has ordered **stops** with pickup and drop times. The fee is
  per term: the stop's own fee if it has one (a far stop costs more), or
  else the route's.
- **Assignment** puts a student or staff member on a route at a stop, both
  ways or one way only, from a date. The route row is locked while
  assigning; once as many riders are on it that day as the vehicle has
  seats, the next gets `409 route_full`. Concurrent requests for the last
  seat were checked live. Ending an assignment records the last day, so
  the seat frees from the next day. A person has one open assignment, and
  a new one can't overlap an old one.
- **Trips.** The route's own crew opens today's (or a past day's) pickup or
  drop run, with no permission needed, the way a teacher marks their own
  class. Anyone holding `transport.manage` for the campus can do it too.
  Opening twice returns the same trip. The trip copies the route's vehicle
  and crew so a later swap doesn't rewrite history. Its **roster** is
  everyone riding that day in that direction, in stop order. Marking
  someone *absent* notifies their parents, once. A *completed* trip can
  still be corrected, but only by the office.
- **Upkeep.** A maintenance log (service, repair, inspection, tyres;
  odometer, cost, vendor, next due by date or kilometres) and a fuel log
  per vehicle.

## Fees

`POST hostel/allocations/generate-invoices/` and
`POST transport/assignments/generate-invoices/` take a term (and optionally
one building or route). For every student who stays or rides any part of
the term:

- each stay or assignment is charged its rate, **prorated by the days it
  covers** (`finance.services.prorate`, inclusive dates; a whole-term stay
  pays exactly the rate). A room move gives two lines that add up.
- the invoice goes through `finance.services.generate_service_invoice`,
  with `source` = `hostel` / `transport`. Each source bills a student
  **once per term**, so rerunning only bills newcomers. A hostel or bus
  invoice never stops the tuition run, and vice versa: finance's own
  "already invoiced" check now looks at `source = fees` only.
- **Scholarships:** only grants aimed at the charged fee category apply
  (e.g. a "Hostel concession" on the Hostel fee category). An
  organization-wide merit scholarship discounts tuition, not a bus fare.
- Room types or routes with no fee category are skipped and named in the
  reply. Cancelled reservations aren't billed; a reservation starting later
  in the term is billed for its days.
- It needs `hostel.manage` / `transport.manage` **and** `finance.manage`,
  and only bills campuses where the caller holds both.

Payments, receipts, refunds, installments and late fees then work as for
any invoice.

## Permissions

| Permission | campus-admin |
|---|:-:|
| `hostel.view` | ✓ |
| `hostel.manage`: buildings, rooms, allocation, check-in/out, moves, complaints | ✓ |
| `transport.view` | ✓ |
| `transport.manage`: fleet, crew, routes, riders, upkeep, correcting any trip | ✓ |

`org-admin` holds everything. A warden's office or a transport office is a
role the organization defines (e.g. `hostel.view` + `hostel.manage`).

No permission is needed for the person the record is about:

- `GET hostel/allocations/me/` and `GET transport/assignments/me/`: your
  own bed or route (as a student or staff member), and a parent's
  children's.
- `GET|POST hostel/complaints/me/`: complaints you raised; file one about
  your room.
- `GET transport/trip-records/me/`: your own or your children's boarding
  history.
- `GET transport/trips/mine/`, `POST transport/trips/` and
  `{id}/mark/` / `{id}/complete/` for a route's own crew.

Campus scoping works as everywhere else: a branch's campus-admin sees and
changes only the branch's buildings, vehicles, routes, riders and trips.
Someone at another campus is refused during validation, so the reply can't
reveal who holds a bed.

## Changes outside the two modules

- `finance.Invoice.source` (migration `finance.0002_invoice_source`;
  existing rows become `fees`), with a `?source=` filter on invoices.
  `generate_service_invoice` and `prorate` were added to finance services.
  `generate_term_invoices` only counts `fees` invoices as "already billed".
- The OpenAPI enum names `GenderEnum` and `DirectionEnum` are pinned in
  `ENUM_NAME_OVERRIDES`. The new `gender`/`direction` fields would otherwise
  have renamed them in the schema, which breaks generated clients.
- The tenant sweep has a row of every new model in `build_tenant()`, the
  new filter names, and attacks for each input serializer: allocate, move,
  complaints, assign, open trip, mark, generate invoices.

## Testing

- **41 module tests** (`modules/hostel/tests`, `modules/transport/tests`):
  - gender, capacity, double allocation, too-early check-in, moves and
    history
  - in-use deletes, case-insensitive codes, campus scoping and tenant
    isolation
  - self-service for residents, staff and parents
  - complaints with notifications
  - crew-only trips, rosters by direction, absence notices sent once,
    office-only correction after completion
  - prorated fees, stop fees, scholarship scoping, reruns, and tuition
    billed alongside
- **Cross-tenant sweep** extended to all 104 new operations.
- **Live run on a real server**: a Kathmandu college's hostels and bus
  route through term 2 of 2083/84.
  - eight students, two parents, wardens, a driver and an assistant, and a
    campus-scoped branch admin
  - real concurrent requests for the last bed and the last seat
  - a mid-term joiner, a room move, a reservation for later in the term,
    a merit scholarship that must not touch the bus fare, and a hostel
    concession that must
  - an absence and its correction after completion
  - tuition billed after hostel and transport

  Every invoice total was recomputed in the script from day counts and
  matched: 259 checks, zero server errors. A generic run over all 794 API
  operations of every phase (anonymous, other-tenant replay, filters,
  empty-body fuzzing): 11128 checks, zero server errors.

## Not built (by choice, for now)

- **Booking a bed ahead while someone still holds it.** A bed has one
  reserved-or-checked-in holder; next year's allocation waits until this
  year's checks out.
- **Mess / meal plans, visitor logs, night roll call and leave passes** for
  residents.
- **Live GPS tracking and ETA** for buses. Stops have coordinates ready for
  it.
- **Staff charges** for a staff bed or seat. Use a payroll adjustment.
- **Refunds when someone leaves mid-term.** The invoice was for the whole
  span billed at the time; settle with a finance credit line or refund.
- **Automatic reminders for expiring papers and licences.** The
  `expiring_within` filters exist; a scheduled job would call them (no cron
  in the stack yet, as in attendance).
- **Fuel efficiency and cost reports** per vehicle. The logs hold
  everything needed.
