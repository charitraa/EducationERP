# Alumni and Careers

What happens after school: the people who graduated, and the jobs, both
the school's own hiring and openings elsewhere for students and alumni.
Two modules, following the conventions in [`identity.md`](identity.md):
`backend/modules/alumni/` and `backend/modules/careers/`. A shared
file-upload foundation, `backend/core/files/`, arrives with them, because
a job application needs a résumé.

```
students.change_student_status(graduated) ──signal──► AlumniProfile (copied: program, level, year, campus)
                                                         ├── Employment, HigherStudy, Achievement   (kept by the graduate)
                                                         ├── Rsvp ──► AlumniEvent
                                                         ├── Mentorship (mentor) ◄── a student or another graduate
                                                         └── Donation ──► Campaign      DonationRefund (new rows)

Vacancy (campus, job form, openings) ──► Candidacy ──► applications.Application (kind "job": steps, history)
                                            ├── résumé ──► files.StoredFile (private)
                                            ├── Interview (panel of staff) ── outcome, score, recommendation
                                            └── JobOffer (made → accepted | declined | withdrawn)
         final approval with an accepted offer ──► staff.StaffMember + hr.Contract

JobPosting (outside openings; alumni post, the office approves) ──► students and/or alumni
```

## Decisions (agreed before building)

| Question | Choice |
|---|---|
| Who keeps alumni records | **The graduate, self-service.** Their login stays, as `user_type` `alumni`. They keep their own profile, jobs, studies and achievements, and can offer to mentor. The office sees and corrects everything, and adds alumni from before the system. |
| What careers covers | **Both.** The school's own hiring (vacancy → application → screening → interview → offer → hired, which creates the staff member and their HR contract), and a job board of outside openings for students and alumni. |
| Donations | **Their own ledger.** Campaigns, and gifts recorded by the office with a receipt number. Refunds are new rows and nothing is edited, as with finance payments. Kept apart from student fee invoices, and no payment gateway, as in finance. |
| Résumés | **The shared upload foundation, built now**: size and type checks, private storage, access checks. Résumés are its first user, and a structured résumé is kept alongside the file. |
| Job applications | **Through the applications engine.** A vacancy's job form supplies the approval chain. |

## Files (`core/files`)

- `POST /files/` (multipart: `file`, `purpose`) stores an upload. Its
  **type comes from the bytes**: PDF, PNG, JPEG and Word (.docx) exist,
  and each purpose accepts some of them. A résumé is PDF or Word. HTML,
  SVG and everything else is refused, so a download can never run script.
  A .docx with macros is refused. The extension must agree with the bytes.
  The size limit is `FILE_UPLOAD_MAX_BYTES` (5 MB).
- **Private by construction.** Bytes live in the `private` storage
  (`PRIVATE_MEDIA_ROOT`, outside `MEDIA_ROOT`) under a generated name. The
  uploaded name is kept for display only. There is no public URL.
  `GET /files/{id}/download/` serves the file as an attachment, with the
  sniffed type, `nosniff`, `no-store`, and a sandboxing CSP.
- **Who may read a file.** Until it is attached, only its uploader. Once a
  module attaches it (a résumé to a candidacy), that module's registered
  check decides (`core.files.access.register`). An owner type with no
  check is readable by nobody, so a forgotten registration fails closed.
  Only the uploader can attach a file, and only once, so nobody can pull
  someone else's upload into their own record.
- The uploader may delete a file nothing uses yet. Attached files stay
  with their record. Uploads are limited per user (`THROTTLE_UPLOADS`,
  30/hour).
- The storage backend is a setting (`STORAGES["private"]`), so it can
  move to S3-compatible storage without touching the modules. Docker
  gets a `private_files` volume.

## Alumni

- **Graduation makes the profile.** `students.change_student_status` now
  sends `student_graduated` (a new signal in `students/signals.py`), inside
  its transaction. Alumni listens and copies name, contacts, campus, and
  what they finished (program, level, section, academic year, date), so a
  later rename doesn't rewrite it. A linked login becomes
  `user_type=alumni`. The students module doesn't know alumni exists.
- `POST alumni/profiles/graduate/` graduates a whole class (`section`)
  or a list of `students` through that same service. A student who can't
  graduate (suspended, a move scheduled) is listed under `skipped` with
  the reason, and the rest go ahead.
- **Self-service.** `GET|PATCH alumni/profiles/me/` changes contact
  details, city, links, bio, directory listing and mentoring offer, but not
  name, campus or what they finished. `alumni/employments/`,
  `higher-studies/` and `achievements/` are the graduate's own (`profile`
  defaults to theirs). The office keeps everyone's at its campuses.
- **Directory.** `alumni/profiles/directory/` lists graduates who chose
  `directory_visible`, for other alumni and the office. It shows no email
  or phone, and can't be searched by them either.
- **Events.** Reunions and talks, campus or organization-wide: draft →
  published → cancelled. Alumni see `events/upcoming/` and answer with
  `rsvp` (going / maybe / not going, guests). Capacity counts guests.
  The event row is locked, so the last place goes once. Cancelling tells
  everyone who hadn't declined. Its own small model, because events
  are about enrolled students' attendance and points.
- **Mentoring.** A graduate offers to mentor (`is_mentor`, topics,
  capacity). `profiles/mentors/` lists them for students and alumni, with
  places left. A student or a graduate asks (`POST mentorships/`). The
  mentor accepts or declines, and capacity is checked under a lock on the
  mentor. Either side ends it, and the mentee can withdraw a request still
  pending. Nobody mentors themselves, and nobody asks a mentor who has no
  account to answer from.
- **Giving.** Campaigns, each for a campus or the whole organization. The
  office records a gift (`DON-000001`, numbered from all rows ever, so a
  number is never reused), for an alumnus or any named donor. A refund is
  a `DonationRefund`, never an edit. It can't exceed what's left on the
  gift or be dated before it. Gift and campaign totals are kept under row
  locks. A donor sees their own gifts at `donations/me/`, and anyone signed
  in sees the open campaigns.

## Careers

### Own hiring

1. **Set up.** A job form is an `ApplicationType` of kind `job` whose last
   step names `careers.hire`, e.g. *Screening* (`careers.manage`) →
   *Principal* (`careers.hire`). A **vacancy** points at it, with campus,
   position, department, staff type, contract kind, openings,
   requirements and dates. It starts as a draft, and `open` checks the
   form is ready. A public vacancy needs a public form.
2. **Apply.** Anyone, at
   `POST public/organizations/{code}/careers/vacancies/{id}/apply/`, as
   multipart with `data` (JSON) and `resume` (the file). Or a signed-in
   alumnus or outsider, at `careers/vacancies/{id}/apply/`, with a
   `resume_file` they uploaded. The candidate's `data` is checked like any
   application's: names, a contact, and a structured résumé (summary,
   education, experience, skills). One open application per email per
   vacancy; the vacancy row is locked while checking. Current staff are
   refused (`already_staff`): moving posts is HR's job. A job form can't be
   submitted through the generic `/applications/` forms.
3. **Decide.** Each step goes through the application:
   `/applications/{id}/approve|reject|send-back/`, with the full history.
   The careers office records a screening score
   (`careers/candidacies/{id}/screen/`).
4. **Interview.** `careers/interviews/` schedules a round with a staff
   panel. The candidate (by email or SMS when they have no account) and
   the panel are told. Panelists see theirs at `interviews/mine/` and
   record the outcome (completed or no-show, score, recommendation,
   feedback), but only once it has started. The office can also move or
   cancel an interview.
5. **Offer.** At the last step, a `careers.hire` holder makes an offer:
   start date, contract kind, probation, end date, salary note, expiry.
   One live offer at a time. The candidate sees it and accepts or declines
   (`offers/{id}/respond/` signed in, or
   `public/.../careers/offer/` and `offer/respond/` with their number and
   token). An expired offer can't be accepted.
6. **Hire.** Approving the last step needs the new `employee_number` in
   `decision` and an accepted offer. The handler creates the `StaffMember`
   (through the new `staff.services.create_staff_member`) and the HR
   `Contract` (through the new `hr.services.start_contract`), in the
   approval's transaction. A taken number, a filled vacancy or a contract
   overlap refuses the approval and creates nothing. A signed-in alumnus's
   login is linked and becomes `staff` or `teacher`. When the last opening
   is filled the vacancy becomes `filled`.

Who sees a candidate and their résumé: the careers office for the
vacancy's campus, their interview panel, whoever decides their current
step, and the candidate.

### Job board

Outside openings: title, company, kind (full-time, part-time,
internship, contract, volunteer), how to apply (a link, an email or
instructions), closing date, and audience (students, alumni or both).

- Alumni post, and the posting waits for a `careers.board` holder to
  approve it. A rejection needs a reason, and the poster is told either
  way. The office posts straight to the board.
- Students see postings for students; alumni see postings for alumni;
  staff see all. Only approved postings show, and only until they close.
- The poster can edit while it waits and can take it down any time.

## Permissions

| Permission | campus-admin |
|---|:-:|
| `alumni.view`: alumni, their events, mentoring and gifts at a campus | ✓ |
| `alumni.manage`: keep records, graduate classes, run events and campaigns | ✓ |
| `alumni.donations`: record gifts and refunds | ✓ |
| `careers.view`: vacancies, candidates, interviews, offers | ✓ |
| `careers.manage`: vacancies, screening, interviews | ✓ |
| `careers.hire`: offers; a job form's last step | ✓ |
| `careers.board`: moderate the job board | ✓ |

`org-admin` holds everything. Graduates, candidates, interviewers,
mentors and mentees need no permission for their own part.

## Changes outside the two modules

- **`core/files`**, a new core app, with `STORAGES["private"]`,
  `PRIVATE_MEDIA_ROOT`, `FILE_UPLOAD_MAX_BYTES` and `THROTTLE_UPLOADS`
  (all in `.env.example`). The Docker image and both compose files have
  a `private_files` volume at `/app/private`. `backend/private/` is
  git-ignored.
- **students**: the `student_graduated` signal (`signals.py`), sent by
  `change_student_status`.
- **accounts**: `User.Type.ALUMNI` (migration `0004_alumni_user_type`).
- **notices**: an `alumni` audience (migration `0002_alumni_audience`).
  Without it a graduate's login would have fallen through to staff
  notices.
- **applications**: kind `job` (migration `0002_job_kind`). Public forms
  may now be admission or job. A kind can be marked as not directly
  submittable, which is how job is sent through careers. Data is stored
  JSON-safe all the way down, including dates inside a résumé.
- **staff** `services.create_staff_member` and **hr**
  `services.start_contract`, with the same rules as their serializers.
- OpenAPI enum names for every new choice set are pinned, and existing
  names are unchanged (`AudienceEnum`, `StaffTypeEnum`, `UserTypeEnum`
  pinned as they were). `tests/test_schema.py` fails on any schema
  warning. It caught three clashing serializer names while this was
  built.
- The tenant sweep has a row of every new model, the new filter names,
  and attacks for each input serializer (graduate, mentorship, donation,
  apply with someone else's file, interview panel, offer). The public
  careers views have their own tenant tests.

## Testing

- **37 tests**:
  - `core/files` (10): accepted types; HTML, SVG and macro documents
    refused; wrong extension, size and purpose; name cleaning; private
    storage; uploader-only access; attaching once; owner rules; deleting.
  - `modules/alumni` (15): graduation, single and whole class; alumni
    notices; self-service limits; office scope; directory privacy;
    mentoring capacity and refusals; RSVP capacity with guests; event
    scope; donation receipts, refunds and totals.
  - `modules/careers` (12): vacancy rules; public and signed-in applying;
    duplicate and résumé refusals with nothing left behind; the whole hire
    from screening to staff member and contract; résumé access; declined
    and withdrawn offers; an alumna hired and her login switched; the job
    board's moderation and audiences; public cross-tenant checks.
- 1387 tests outside the sweep pass, plus the cross-tenant sweep.

## Not built (by choice, for now)

- **Internal promotions through careers.** Staff are refused at apply;
  changing someone's post is an HR contract change.
- **Online giving.** No payment gateway yet, as in finance. Gifts are
  recorded by the office.
- **Interview calendars and invitations** (.ics, video links made for
  you). The location field holds a room or a link.
- **Bulk alumni import** from paper registers. Use `POST alumni/profiles/`
  per person for now, as with the bulk Excel import still queued for
  students.
- **Virus scanning** of uploads. Types are limited to PDF, Word and
  images, and are served as downloads only. A scanner belongs behind the
  storage adapter when the deployment has one.
- **Upload quotas per organization.** The per-user upload throttle is the
  guard until the SaaS quota work.
