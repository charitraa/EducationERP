# API endpoint index

Generated from `openapi.yaml` (944 operations). Base URL: `/api/v1`. Full request and response
shapes are in `openapi.yaml`; this list is short enough to paste into a chat.

## academics

| Method | Path | What it does |
|---|---|---|
| POST | `/departments/` | Create a department |
| GET | `/departments/` | List departments |
| PATCH | `/departments/{id}/` | Update a department |
| PUT | `/departments/{id}/` | Replace a department |
| DELETE | `/departments/{id}/` | Delete a department |
| GET | `/departments/{id}/` | Retrieve a department |
| POST | `/programs/` | Create a program |
| GET | `/programs/` | List programs |
| PATCH | `/programs/{id}/` | Update a program |
| PUT | `/programs/{id}/` | Replace a program |
| DELETE | `/programs/{id}/` | Delete a program |
| GET | `/programs/{id}/` | Retrieve a program |
| POST | `/subjects/` | Create a subject |
| GET | `/subjects/` | List subjects |
| PATCH | `/subjects/{id}/` | Update a subject |
| PUT | `/subjects/{id}/` | Replace a subject |
| DELETE | `/subjects/{id}/` | Delete a subject |
| GET | `/subjects/{id}/` | Retrieve a subject |
| POST | `/curriculum/` | Create a curriculum entry |
| GET | `/curriculum/` | List curriculum entrys |
| PATCH | `/curriculum/{id}/` | Update a curriculum entry |
| PUT | `/curriculum/{id}/` | Replace a curriculum entry |
| DELETE | `/curriculum/{id}/` | Delete a curriculum entry |
| GET | `/curriculum/{id}/` | Retrieve a curriculum entry |
| POST | `/academic-years/` | Create a academic year |
| GET | `/academic-years/` | List academic years |
| PATCH | `/academic-years/{id}/` | Update a academic year |
| PUT | `/academic-years/{id}/` | Replace a academic year |
| DELETE | `/academic-years/{id}/` | Delete a academic year |
| GET | `/academic-years/{id}/` | Retrieve a academic year |
| POST | `/academic-years/{id}/set-current/` | Make this the current academic year |
| POST | `/terms/` | Create a term |
| GET | `/terms/` | List terms |
| PATCH | `/terms/{id}/` | Update a term |
| PUT | `/terms/{id}/` | Replace a term |
| DELETE | `/terms/{id}/` | Delete a term |
| GET | `/terms/{id}/` | Retrieve a term |
| POST | `/rooms/` | Create a room |
| GET | `/rooms/` | List rooms |
| PATCH | `/rooms/{id}/` | Update a room |
| PUT | `/rooms/{id}/` | Replace a room |
| DELETE | `/rooms/{id}/` | Delete a room |
| GET | `/rooms/{id}/` | Retrieve a room |
| POST | `/batches/` | Create a batch |
| GET | `/batches/` | List batchs |
| PATCH | `/batches/{id}/` | Update a batch |
| PUT | `/batches/{id}/` | Replace a batch |
| DELETE | `/batches/{id}/` | Delete a batch |
| GET | `/batches/{id}/` | Retrieve a batch |
| POST | `/sections/` | Create a section |
| GET | `/sections/` | List sections |
| PATCH | `/sections/{id}/` | Update a section |
| PUT | `/sections/{id}/` | Replace a section |
| DELETE | `/sections/{id}/` | Delete a section |
| GET | `/sections/{id}/` | Retrieve a section |
| POST | `/sections/{id}/promote/` | Move a whole class to another section |
| GET | `/sections/{id}/students/` | Students currently in a section |
| POST | `/teaching-assignments/` | Create a teaching assignment |
| GET | `/teaching-assignments/` | List teaching assignments |
| PATCH | `/teaching-assignments/{id}/` | Update a teaching assignment |
| PUT | `/teaching-assignments/{id}/` | Replace a teaching assignment |
| DELETE | `/teaching-assignments/{id}/` | Delete a teaching assignment |
| GET | `/teaching-assignments/{id}/` | Retrieve a teaching assignment |
| POST | `/student-electives/` | Record that a student takes an elective |
| GET | `/student-electives/` | List students' elective choices |
| DELETE | `/student-electives/{id}/` | Remove an elective choice |
| GET | `/student-electives/{id}/` | Retrieve an elective choice |
| POST | `/calendar/` | Create a calendar event |
| GET | `/calendar/` | List calendar events |
| PATCH | `/calendar/{id}/` | Update a calendar event |
| PUT | `/calendar/{id}/` | Replace a calendar event |
| DELETE | `/calendar/{id}/` | Delete a calendar event |
| GET | `/calendar/{id}/` | Retrieve a calendar event |

## admissions

| Method | Path | What it does |
|---|---|---|
| POST | `/admissions/` | Record an application |
| GET | `/admissions/` | List applications |
| PATCH | `/admissions/{id}/` | Update a pending application |
| PUT | `/admissions/{id}/` | Replace a pending application |
| DELETE | `/admissions/{id}/` | Soft-delete an application that did not enroll |
| GET | `/admissions/{id}/` | Retrieve an application |
| POST | `/admissions/{id}/approve/` | Approve an application |
| POST | `/admissions/{id}/enroll/` | Enroll an approved applicant: creates the student and their guardian |
| POST | `/admissions/{id}/reject/` | Reject an application (note required) |
| POST | `/admissions/{id}/withdraw/` | Withdraw an application |

## admit-cards

| Method | Path | What it does |
|---|---|---|
| POST | `/admit-cards/` | Admit cards are only ever made by ``generate-admit-cards``; there is |

## alumni

| Method | Path | What it does |
|---|---|---|
| POST | `/alumni/profiles/` | Create a alumni profile |
| GET | `/alumni/profiles/` | List alumni profiles |
| GET | `/alumni/profiles/directory/` | Alumni directory (those who chose to be listed) |
| POST | `/alumni/profiles/graduate/` | Graduate a class or a list of students |
| PATCH | `/alumni/profiles/me/` | Update my alumni profile |
| GET | `/alumni/profiles/me/` | My alumni profile |
| GET | `/alumni/profiles/mentors/` | Alumni taking mentees |
| PATCH | `/alumni/profiles/{id}/` | Update a alumni profile |
| PUT | `/alumni/profiles/{id}/` | Replace a alumni profile |
| DELETE | `/alumni/profiles/{id}/` | Delete a alumni profile |
| GET | `/alumni/profiles/{id}/` | Retrieve a alumni profile |
| POST | `/alumni/employments/` | Create a employment record |
| GET | `/alumni/employments/` | List employment records |
| PATCH | `/alumni/employments/{id}/` | Update a employment record |
| PUT | `/alumni/employments/{id}/` | Replace a employment record |
| DELETE | `/alumni/employments/{id}/` | Delete a employment record |
| GET | `/alumni/employments/{id}/` | Retrieve a employment record |
| POST | `/alumni/higher-studies/` | Create a higher study record |
| GET | `/alumni/higher-studies/` | List higher study records |
| PATCH | `/alumni/higher-studies/{id}/` | Update a higher study record |
| PUT | `/alumni/higher-studies/{id}/` | Replace a higher study record |
| DELETE | `/alumni/higher-studies/{id}/` | Delete a higher study record |
| GET | `/alumni/higher-studies/{id}/` | Retrieve a higher study record |
| POST | `/alumni/achievements/` | Create a achievement |
| GET | `/alumni/achievements/` | List achievements |
| PATCH | `/alumni/achievements/{id}/` | Update a achievement |
| PUT | `/alumni/achievements/{id}/` | Replace a achievement |
| DELETE | `/alumni/achievements/{id}/` | Delete a achievement |
| GET | `/alumni/achievements/{id}/` | Retrieve a achievement |
| POST | `/alumni/events/` | Create a alumni event |
| GET | `/alumni/events/` | List alumni events |
| GET | `/alumni/events/upcoming/` | Events I can come to |
| PATCH | `/alumni/events/{id}/` | Update a alumni event |
| PUT | `/alumni/events/{id}/` | Replace a alumni event |
| DELETE | `/alumni/events/{id}/` | Delete a alumni event |
| GET | `/alumni/events/{id}/` | Retrieve a alumni event |
| POST | `/alumni/events/{id}/cancel/` | Cancel the event (those coming are told) |
| POST | `/alumni/events/{id}/publish/` | Publish the event |
| POST | `/alumni/events/{id}/rsvp/` | Say whether I'm coming |
| GET | `/alumni/events/{id}/rsvps/` | Who is coming |
| POST | `/alumni/mentorships/` | Ask an alumnus to mentor me |
| GET | `/alumni/mentorships/` | Mentoring I'm part of (the office: everyone's at its campuses) |
| GET | `/alumni/mentorships/{id}/` | A mentoring request |
| POST | `/alumni/mentorships/{id}/accept/` | Accept (mentor) |
| POST | `/alumni/mentorships/{id}/decline/` | Decline (mentor) |
| POST | `/alumni/mentorships/{id}/end/` | Withdraw a request, or end a mentorship (either side) |
| POST | `/alumni/campaigns/` | Create a donation campaign |
| GET | `/alumni/campaigns/` | List donation campaigns |
| GET | `/alumni/campaigns/open/` | Campaigns open for giving |
| PATCH | `/alumni/campaigns/{id}/` | Update a donation campaign |
| PUT | `/alumni/campaigns/{id}/` | Replace a donation campaign |
| DELETE | `/alumni/campaigns/{id}/` | Delete a donation campaign |
| GET | `/alumni/campaigns/{id}/` | Retrieve a donation campaign |
| POST | `/alumni/donations/` | Record a donation (a receipt number is issued) |
| GET | `/alumni/donations/` | List donations |
| GET | `/alumni/donations/me/` | My gifts |
| GET | `/alumni/donations/{id}/` | A donation with its refunds |
| POST | `/alumni/donations/{id}/refund/` | Refund part or all of a gift (a new row; the gift stays) |

## api keys

| Method | Path | What it does |
|---|---|---|
| POST | `/api-keys/` | Create an API key (the secret is in this response only) |
| GET | `/api-keys/` | List API keys |
| PUT | `/api-keys/{id}/` | Replace an API key's settings |
| PATCH | `/api-keys/{id}/` | Rename, or change read-only, expiry, addresses or rate |
| GET | `/api-keys/{id}/` | Retrieve an API key (never its secret) |
| POST | `/api-keys/{id}/assign-role/` | Give the key a role (no more than you hold yourself) |
| POST | `/api-keys/{id}/revoke/` | Revoke (it stops working at once and stays on record) |
| POST | `/api-keys/{id}/revoke-role/` | Take a role away from the key |
| POST | `/api-keys/{id}/rotate/` | Rotate: a new secret, shown once; the old one stops working |

## applications

| Method | Path | What it does |
|---|---|---|
| POST | `/application-types/` | Create a application type |
| GET | `/application-types/` | List application types |
| GET | `/application-types/available/` | Forms I can fill in |
| PATCH | `/application-types/{id}/` | Update a application type |
| PUT | `/application-types/{id}/` | Replace a application type |
| DELETE | `/application-types/{id}/` | Delete a application type |
| GET | `/application-types/{id}/` | Retrieve a application type |
| POST | `/applications/` | Submit an application |
| GET | `/applications/` | List applications (office) |
| GET | `/applications/me/` | Applications I sent, or that are about me or my children |
| GET | `/applications/pending/` | Applications waiting for my decision |
| GET | `/applications/{id}/` | An application with its history |
| POST | `/applications/{id}/approve/` | Approve the current step (the last one carries the request out) |
| POST | `/applications/{id}/reject/` | Reject (a reason is required) |
| POST | `/applications/{id}/resubmit/` | Resubmit after it was sent back |
| POST | `/applications/{id}/send-back/` | Send back to the applicant to change |
| POST | `/applications/{id}/withdraw/` | Withdraw while it's open |
| POST | `/certificates/` | Issue a certificate directly |
| GET | `/certificates/` | List certificates |
| GET | `/certificates/me/` | My (or my children's) certificates |
| GET | `/certificates/{id}/` | Retrieve a certificate |
| POST | `/certificates/{id}/revoke/` | Revoke (it stays on record) |

## attendance

| Method | Path | What it does |
|---|---|---|
| POST | `/attendance/device-punches/` | Send punches from a device (device API key) |
| POST | `/attendance/sessions/` | Open a lesson's or a class's attendance |
| GET | `/attendance/sessions/` | List attendance sessions |
| GET | `/attendance/sessions/mine/` | My classes to take attendance for on a day |
| POST | `/attendance/sessions/scan/` | Scan a class's QR code (students) |
| GET | `/attendance/sessions/{id}/` | Retrieve a attendance session |
| POST | `/attendance/sessions/{id}/mark/` | Mark students |
| POST | `/attendance/sessions/{id}/qr/` | A QR code for students to scan |
| POST | `/attendance/sessions/{id}/reopen/` | Reopen submitted attendance (office) |
| GET | `/attendance/sessions/{id}/roster/` | Who is expected, and how each is marked so far |
| POST | `/attendance/sessions/{id}/submit/` | Submit the attendance |
| GET | `/attendance/records/` | List attendance records |
| GET | `/attendance/records/me/` | My attendance (students), or a child's (parents) |
| PATCH | `/attendance/records/{id}/` | Change or correct one record |
| GET | `/attendance/records/{id}/` | Retrieve a attendance record |
| GET | `/attendance/reports/defaulters/` | Students below an attendance percentage |
| GET | `/attendance/reports/missing/` | Attendance not taken or not submitted on a day |
| GET | `/attendance/reports/register/` | A class's register: students × sessions, with totals |
| GET | `/attendance/reports/staff/` | Staff attendance: days present, late, absent … |
| GET | `/attendance/reports/student/` | One student's attendance, overall and per subject |
| POST | `/attendance/staff-days/` | Set a staff member's day by hand |
| GET | `/attendance/staff-days/` | List staff attendance days |
| GET | `/attendance/staff-days/me/` | My attendance (staff) |
| DELETE | `/attendance/staff-days/{id}/` | Delete a staff attendance day |
| GET | `/attendance/staff-days/{id}/` | Retrieve a staff attendance day |
| POST | `/attendance/punches/` | Enter a staff punch by hand (office) |
| GET | `/attendance/punches/` | List punchs |
| POST | `/attendance/punches/check-in/` | Check in or out by scanning the campus QR (staff) |
| POST | `/attendance/punches/qr/` | A QR code for staff to check in with, at a campus |
| GET | `/attendance/punches/{id}/` | Retrieve a punch |
| POST | `/attendance/work-schedules/` | Create a work schedule |
| GET | `/attendance/work-schedules/` | List work schedules |
| PATCH | `/attendance/work-schedules/{id}/` | Update a work schedule |
| PUT | `/attendance/work-schedules/{id}/` | Replace a work schedule |
| DELETE | `/attendance/work-schedules/{id}/` | Delete a work schedule |
| GET | `/attendance/work-schedules/{id}/` | Retrieve a work schedule |
| POST | `/attendance/staff-schedules/` | Create a staff work schedule |
| GET | `/attendance/staff-schedules/` | List staff work schedules |
| PATCH | `/attendance/staff-schedules/{id}/` | Update a staff work schedule |
| PUT | `/attendance/staff-schedules/{id}/` | Replace a staff work schedule |
| DELETE | `/attendance/staff-schedules/{id}/` | Delete a staff work schedule |
| GET | `/attendance/staff-schedules/{id}/` | Retrieve a staff work schedule |
| POST | `/attendance/devices/` | Create a attendance device |
| GET | `/attendance/devices/` | List attendance devices |
| PATCH | `/attendance/devices/{id}/` | Update a attendance device |
| PUT | `/attendance/devices/{id}/` | Replace a attendance device |
| DELETE | `/attendance/devices/{id}/` | Delete a attendance device |
| GET | `/attendance/devices/{id}/` | Retrieve a attendance device |
| POST | `/attendance/devices/{id}/rotate-key/` | Issue a new API key (generic devices) |
| POST | `/attendance/biometric-ids/` | Create a biometric identity |
| GET | `/attendance/biometric-ids/` | List biometric identitys |
| PATCH | `/attendance/biometric-ids/{id}/` | Update a biometric identity |
| PUT | `/attendance/biometric-ids/{id}/` | Replace a biometric identity |
| DELETE | `/attendance/biometric-ids/{id}/` | Delete a biometric identity |
| GET | `/attendance/biometric-ids/{id}/` | Retrieve a biometric identity |

## audit

| Method | Path | What it does |
|---|---|---|
| GET | `/audit-logs/` | List audit log entries |
| GET | `/audit-logs/{id}/` | Retrieve an audit log entry |

## auth

| Method | Path | What it does |
|---|---|---|
| POST | `/auth/login/` | Log in and obtain a token pair |
| POST | `/auth/refresh/` | Exchange a refresh token for a new access token |
| POST | `/auth/logout/` | Log out |
| GET | `/auth/me/` | Current user |
| PATCH | `/auth/me/` | Update own contact details |
| POST | `/auth/change-password/` | Change own password |
| GET | `/auth/2fa/` | Is two-factor login on for me? |
| POST | `/auth/2fa/setup/` | Start two-factor setup: returns the secret to add to an authenticator app |
| POST | `/auth/2fa/confirm/` | Finish setup with a code from the app; returns recovery codes (shown once) |
| POST | `/auth/2fa/recovery-codes/` | Replace all recovery codes (needs a current app code) |
| POST | `/auth/2fa/disable/` | Turn two-factor login off (needs password and a code) |
| POST | `/auth/password-reset/` | Forgot password: emails a reset link (same answer whether or not the account exists) |
| POST | `/auth/password-reset/confirm/` | Choose a new password from the emailed link (signs out everywhere) |

## careers

| Method | Path | What it does |
|---|---|---|
| POST | `/careers/vacancies/` | Create a vacancy (as a draft) |
| GET | `/careers/vacancies/` | List vacancies |
| GET | `/careers/vacancies/current/` | Vacancies I can apply to |
| PATCH | `/careers/vacancies/{id}/` | Update a vacancy |
| PUT | `/careers/vacancies/{id}/` | Replace a vacancy |
| DELETE | `/careers/vacancies/{id}/` | Delete a draft vacancy nobody applied to |
| GET | `/careers/vacancies/{id}/` | Retrieve a vacancy |
| POST | `/careers/vacancies/{id}/apply/` | Apply (signed in; upload the résumé to /files/ first) |
| POST | `/careers/vacancies/{id}/close/` | Stop taking applications |
| POST | `/careers/vacancies/{id}/open/` | Open it for applications |
| POST | `/careers/candidacies/` | Read-only: a candidacy is made by applying, and decided through its |
| GET | `/careers/candidacies/` | List candidates |
| GET | `/careers/candidacies/{id}/` | A candidate with their application data |
| POST | `/careers/candidacies/{id}/screen/` | Record a screening score and note |
| POST | `/careers/interviews/` | Schedule an interview (the candidate and panel are told) |
| GET | `/careers/interviews/` | List interviews |
| GET | `/careers/interviews/mine/` | Interviews I'm on the panel for |
| GET | `/careers/interviews/{id}/` | Retrieve an interview |
| POST | `/careers/interviews/{id}/cancel/` | Cancel it (the candidate is told) |
| POST | `/careers/interviews/{id}/outcome/` | Record how it went (the panel, or the office) |
| POST | `/careers/interviews/{id}/reschedule/` | Move it |
| POST | `/careers/offers/` | Make an offer (at the last step; the candidate is told) |
| GET | `/careers/offers/` | List job offers |
| GET | `/careers/offers/mine/` | Offers made to me |
| GET | `/careers/offers/{id}/` | Retrieve a job offer |
| POST | `/careers/offers/{id}/respond/` | Accept or decline an offer made to me |
| POST | `/careers/offers/{id}/withdraw/` | Withdraw the offer |
| POST | `/careers/postings/` | Post a job (alumni: waits for approval) |
| GET | `/careers/postings/` | The job board (moderators also see pending and closed) |
| GET | `/careers/postings/mine/` | Postings I made |
| PUT | `/careers/postings/{id}/` | Replace a posting (its poster, while pending; or a moderator) |
| PATCH | `/careers/postings/{id}/` | Update a posting (its poster, while pending; or a moderator) |
| GET | `/careers/postings/{id}/` | A job posting |
| POST | `/careers/postings/{id}/close/` | Take it down (its poster or a moderator) |
| POST | `/careers/postings/{id}/review/` | Approve or reject (moderator) |

## communication

| Method | Path | What it does |
|---|---|---|
| POST | `/communication/threads/` | Start a conversation (staff) |
| GET | `/communication/threads/` | My conversations |
| GET | `/communication/threads/{id}/` | One conversation |
| POST | `/communication/threads/{id}/close/` | Close the conversation |
| POST | `/communication/threads/{id}/messages/` | Read or send a message |
| GET | `/communication/threads/{id}/messages/` | Read or send a message |
| POST | `/communication/appointment-slots/` | Publish an appointment slot |
| GET | `/communication/appointment-slots/` | List appointment slots |
| PATCH | `/communication/appointment-slots/{id}/` | Update an appointment slot |
| PUT | `/communication/appointment-slots/{id}/` | Replace an appointment slot |
| DELETE | `/communication/appointment-slots/{id}/` | Delete an appointment slot |
| GET | `/communication/appointment-slots/{id}/` | Retrieve an appointment slot |
| POST | `/communication/appointment-slots/{id}/cancel/` | Cancel the slot |
| POST | `/communication/appointments/` | Book an open slot |
| GET | `/communication/appointments/` | List appointments |
| GET | `/communication/appointments/{id}/` | Retrieve an appointment |
| POST | `/communication/appointments/{id}/approve/` | Approve the appointment |
| POST | `/communication/appointments/{id}/cancel/` | Cancel the appointment |
| POST | `/communication/appointments/{id}/complete/` | Mark the appointment completed |

## events

| Method | Path | What it does |
|---|---|---|
| POST | `/event-categories/` | Create a event category |
| GET | `/event-categories/` | List event categorys |
| PATCH | `/event-categories/{id}/` | Update a event category |
| PUT | `/event-categories/{id}/` | Replace a event category |
| DELETE | `/event-categories/{id}/` | Delete a event category |
| GET | `/event-categories/{id}/` | Retrieve a event category |
| POST | `/events/` | Create a event |
| GET | `/events/` | List events |
| GET | `/events/me/` | Events I can register for, or I'm registered for (students, parents) |
| PATCH | `/events/{id}/` | Update a event |
| PUT | `/events/{id}/` | Replace a event |
| DELETE | `/events/{id}/` | Delete a event |
| GET | `/events/{id}/` | Retrieve a event |
| GET | `/events/{id}/attendance/` | Attendance taken so far |
| POST | `/events/{id}/cancel/` | Cancel the event |
| POST | `/events/{id}/mark-attendance/` | Mark attendance |
| GET | `/events/{id}/participation/` | Participation recorded so far |
| POST | `/events/{id}/publish/` | Publish the event |
| POST | `/events/{id}/record-participation/` | Record a student's role (and, for a competition, where they placed) |
| POST | `/events/{id}/register/` | Register for the event (students) |
| GET | `/events/{id}/registrations/` | Who's registered |
| GET | `/events/{id}/roster/` | Everyone tied to the event: registration, attendance, participation |
| POST | `/event-registrations/` | Register a student (organizer or events office) |
| GET | `/event-registrations/` | List registrations |
| GET | `/event-registrations/{id}/` | Retrieve a registration |
| POST | `/event-registrations/{id}/decide/` | Approve or reject a registration |
| POST | `/event-registrations/{id}/withdraw/` | Withdraw your registration (students) |
| POST | `/point-rules/` | Create a point rule |
| GET | `/point-rules/` | List point rules |
| PATCH | `/point-rules/{id}/` | Update a point rule |
| PUT | `/point-rules/{id}/` | Replace a point rule |
| DELETE | `/point-rules/{id}/` | Delete a point rule |
| GET | `/point-rules/{id}/` | Retrieve a point rule |
| POST | `/point-entries/` | Award points by hand |
| GET | `/point-entries/` | List point entrys |
| GET | `/point-entries/{id}/` | Retrieve a point entry |
| GET | `/student-points/` | List student pointss |
| GET | `/student-points/leaderboard/` | Students with the most points |
| GET | `/student-points/me/` | My points and awards (students), or a child's (parents) |
| GET | `/student-points/{student_id}/` | Retrieve a student points |
| POST | `/awards/` | Create a award |
| GET | `/awards/` | List awards |
| PATCH | `/awards/{id}/` | Update a award |
| PUT | `/awards/{id}/` | Replace a award |
| DELETE | `/awards/{id}/` | Delete a award |
| GET | `/awards/{id}/` | Retrieve a award |
| POST | `/award-rules/` | Create a award rule |
| GET | `/award-rules/` | List award rules |
| PATCH | `/award-rules/{id}/` | Update a award rule |
| PUT | `/award-rules/{id}/` | Replace a award rule |
| DELETE | `/award-rules/{id}/` | Delete a award rule |
| GET | `/award-rules/{id}/` | Retrieve a award rule |
| POST | `/student-awards/` | Create a student award |
| GET | `/student-awards/` | List student awards |
| GET | `/student-awards/{id}/` | Retrieve a student award |
| POST | `/student-awards/{id}/end/` | End a student's award (mainly for a title) |

## exams

| Method | Path | What it does |
|---|---|---|
| POST | `/exam-types/` | Create a exam type |
| GET | `/exam-types/` | List exam types |
| PATCH | `/exam-types/{id}/` | Update a exam type |
| PUT | `/exam-types/{id}/` | Replace a exam type |
| DELETE | `/exam-types/{id}/` | Delete a exam type |
| GET | `/exam-types/{id}/` | Retrieve a exam type |
| POST | `/exams/` | Create a exam |
| GET | `/exams/` | List exams |
| GET | `/exams/me/` | My exams (students), or a child's (parents) |
| PATCH | `/exams/{id}/` | Update a exam |
| PUT | `/exams/{id}/` | Replace a exam |
| DELETE | `/exams/{id}/` | Delete a exam |
| GET | `/exams/{id}/` | Retrieve a exam |
| POST | `/exams/{id}/add-curriculum/` | Add a paper for every curriculum subject at some levels |
| POST | `/exams/{id}/clear-seat-plan/` | Remove the seat plan |
| POST | `/exams/{id}/compute/` | Work out the results from the marks so far (not published) |
| POST | `/exams/{id}/generate-admit-cards/` | Issue admit cards to every candidate |
| POST | `/exams/{id}/publish/` | Publish the results |
| GET | `/exams/{id}/readiness/` | What still blocks publishing |
| POST | `/exams/{id}/schedule/` | Schedule the exam |
| POST | `/exams/{id}/seat-plan/` | Give every candidate a room and seat |
| GET | `/exams/{id}/summary/` | How the class did: pass rates, averages, toppers |
| POST | `/exams/{id}/unpublish/` | Withdraw published results |
| POST | `/exams/{id}/unschedule/` | Take the exam back to being set up (only before marks exist) |
| POST | `/exam-subjects/` | Create a exam paper |
| GET | `/exam-subjects/` | List exam papers |
| PATCH | `/exam-subjects/{id}/` | Update a exam paper |
| PUT | `/exam-subjects/{id}/` | Replace a exam paper |
| DELETE | `/exam-subjects/{id}/` | Delete a exam paper |
| GET | `/exam-subjects/{id}/` | Retrieve a exam paper |
| POST | `/exam-rooms/` | Create a exam room |
| GET | `/exam-rooms/` | List exam rooms |
| PATCH | `/exam-rooms/{id}/` | Update a exam room |
| PUT | `/exam-rooms/{id}/` | Replace a exam room |
| DELETE | `/exam-rooms/{id}/` | Delete a exam room |
| GET | `/exam-rooms/{id}/` | Retrieve a exam room |
| GET | `/seat-allocations/` | List seats |
| GET | `/seat-allocations/{id}/` | Retrieve a seat |
| POST | `/invigilations/` | Create a invigilator duty |
| GET | `/invigilations/` | List invigilator dutys |
| PATCH | `/invigilations/{id}/` | Update a invigilator duty |
| PUT | `/invigilations/{id}/` | Replace a invigilator duty |
| DELETE | `/invigilations/{id}/` | Delete a invigilator duty |
| GET | `/invigilations/{id}/` | Retrieve a invigilator duty |
| GET | `/admit-cards/` | List admit cards |
| GET | `/admit-cards/me/` | My admit cards (students), or a child's (parents) |
| GET | `/admit-cards/{id}/` | Retrieve a admit card |
| GET | `/admit-cards/{id}/data/` | Everything printed on the admit card |
| POST | `/admit-cards/{id}/release/` | Release a withheld admit card |
| POST | `/admit-cards/{id}/withhold/` | Withhold an admit card |
| POST | `/mark-sheets/` | Open the mark sheet for a paper and class |
| GET | `/mark-sheets/` | List mark sheets |
| GET | `/mark-sheets/mine/` | My papers to enter marks for |
| GET | `/mark-sheets/{id}/` | Retrieve a mark sheet |
| POST | `/mark-sheets/{id}/marks/` | Enter or change marks |
| GET | `/mark-sheets/{id}/roster/` | Who sits the paper, the components, and the marks so far |
| POST | `/mark-sheets/{id}/send-back/` | Send the marks back to the teacher (exam office) |
| POST | `/mark-sheets/{id}/submit/` | Submit the marks to the exam office |
| POST | `/mark-sheets/{id}/verify/` | Verify submitted marks (exam office) |
| GET | `/marks/` | List marks |
| PATCH | `/marks/{id}/` | Update a mark |
| GET | `/marks/{id}/` | Retrieve a mark |
| GET | `/results/` | List results |
| GET | `/results/me/` | My published results (students), or a child's (parents) |
| GET | `/results/{id}/` | Retrieve a result |
| POST | `/results/{id}/remark/` | Write the class teacher's remark on a result |
| GET | `/results/{id}/report-card/` | A result's report card |
| POST | `/term-results/` | Create a term result |
| GET | `/term-results/` | List term results |
| PATCH | `/term-results/{id}/` | Update a term result |
| PUT | `/term-results/{id}/` | Replace a term result |
| DELETE | `/term-results/{id}/` | Delete a term result |
| GET | `/term-results/{id}/` | Retrieve a term result |
| POST | `/term-results/{id}/compute/` | Work out the term results from the exams (not published) |
| POST | `/term-results/{id}/publish/` | Publish the term results |
| POST | `/term-results/{id}/unpublish/` | Withdraw published term results |
| GET | `/report-cards/` | Report cards for a class or a student |
| GET | `/report-cards/me/` | My report card (students), or a child's (parents) |
| GET | `/transcripts/me/` | My transcript (students), or a child's (parents) |
| GET | `/transcripts/{id}/` | A student's transcript |

## files

| Method | Path | What it does |
|---|---|---|
| POST | `/files/` | Upload a file (multipart: file, purpose) |
| GET | `/files/` | My uploads |
| DELETE | `/files/{id}/` | Remove an upload nothing uses yet |
| GET | `/files/{id}/` | A file's details |
| GET | `/files/{id}/download/` | Download the file |

## finance

| Method | Path | What it does |
|---|---|---|
| POST | `/invoices/assess-late-fees/` | Assess a late fee on every overdue invoice |
| POST | `/fee-categories/` | Create a fee category |
| GET | `/fee-categories/` | List fee categorys |
| PATCH | `/fee-categories/{id}/` | Update a fee category |
| PUT | `/fee-categories/{id}/` | Replace a fee category |
| DELETE | `/fee-categories/{id}/` | Delete a fee category |
| GET | `/fee-categories/{id}/` | Retrieve a fee category |
| POST | `/fee-structures/` | Create a fee structure |
| GET | `/fee-structures/` | List fee structures |
| PATCH | `/fee-structures/{id}/` | Update a fee structure |
| PUT | `/fee-structures/{id}/` | Replace a fee structure |
| DELETE | `/fee-structures/{id}/` | Delete a fee structure |
| GET | `/fee-structures/{id}/` | Retrieve a fee structure |
| POST | `/fee-structures/{id}/generate-one-time-invoice/` | Generate one student's one-time invoice (e.g. admission) |
| POST | `/fee-structures/{id}/generate-invoices/` | Generate this term's invoices from the structure |
| POST | `/scholarships/` | Create a scholarship |
| GET | `/scholarships/` | List scholarships |
| PATCH | `/scholarships/{id}/` | Update a scholarship |
| PUT | `/scholarships/{id}/` | Replace a scholarship |
| DELETE | `/scholarships/{id}/` | Delete a scholarship |
| GET | `/scholarships/{id}/` | Retrieve a scholarship |
| POST | `/student-scholarships/` | Create a student scholarship |
| GET | `/student-scholarships/` | List student scholarships |
| GET | `/student-scholarships/{id}/` | Retrieve a student scholarship |
| POST | `/student-scholarships/{id}/end/` | End a student's scholarship |
| GET | `/invoices/reports/collection/` | Payments received in a span, by method (a day sheet) |
| GET | `/invoices/reports/outstanding/` | Overdue invoices |
| GET | `/invoices/reports/student/` | One student's statement |
| GET | `/invoices/` | List invoices |
| GET | `/invoices/me/` | My invoices (students), or a child's (parents) |
| GET | `/invoices/{id}/` | Retrieve a invoice |
| POST | `/invoices/{id}/add-item/` | Add an ad-hoc line (a discount, a fine, an adjustment) |
| POST | `/invoices/{id}/cancel/` | Cancel an invoice |
| POST | `/invoices/{id}/installments/` | Split the total into due-dated installments |
| POST | `/payments/` | Record a payment against an invoice, and issue a receipt |
| GET | `/payments/` | List payments |
| GET | `/payments/{id}/` | Retrieve a payment |
| POST | `/payments/{id}/refund/` | Refund some or all of a payment |
| GET | `/receipts/` | List receipts |
| GET | `/receipts/{id}/` | Retrieve a receipt |
| GET | `/refunds/` | List refunds |
| GET | `/refunds/{id}/` | Retrieve a refund |

## grades

| Method | Path | What it does |
|---|---|---|
| POST | `/grades/scales/` | Create a grade scale |
| GET | `/grades/scales/` | List grade scales |
| PATCH | `/grades/scales/{id}/` | Update a grade scale |
| PUT | `/grades/scales/{id}/` | Replace a grade scale |
| DELETE | `/grades/scales/{id}/` | Delete a grade scale |
| GET | `/grades/scales/{id}/` | Retrieve a grade scale |
| GET | `/grades/scales/{id}/grade/` | The grade a percentage earns on this scale |

## hostel

| Method | Path | What it does |
|---|---|---|
| POST | `/hostel/buildings/` | Create a building |
| GET | `/hostel/buildings/` | List buildings |
| PATCH | `/hostel/buildings/{id}/` | Update a building |
| PUT | `/hostel/buildings/{id}/` | Replace a building |
| DELETE | `/hostel/buildings/{id}/` | Delete a building |
| GET | `/hostel/buildings/{id}/` | Retrieve a building |
| POST | `/hostel/floors/` | Create a floor |
| GET | `/hostel/floors/` | List floors |
| PATCH | `/hostel/floors/{id}/` | Update a floor |
| PUT | `/hostel/floors/{id}/` | Replace a floor |
| DELETE | `/hostel/floors/{id}/` | Delete a floor |
| GET | `/hostel/floors/{id}/` | Retrieve a floor |
| POST | `/hostel/room-types/` | Create a room type |
| GET | `/hostel/room-types/` | List room types |
| PATCH | `/hostel/room-types/{id}/` | Update a room type |
| PUT | `/hostel/room-types/{id}/` | Replace a room type |
| DELETE | `/hostel/room-types/{id}/` | Delete a room type |
| GET | `/hostel/room-types/{id}/` | Retrieve a room type |
| POST | `/hostel/rooms/` | Tenant-owned resources that also belong to one campus. |
| GET | `/hostel/rooms/` | List rooms |
| PATCH | `/hostel/rooms/{id}/` | Tenant-owned resources that also belong to one campus. |
| PUT | `/hostel/rooms/{id}/` | Tenant-owned resources that also belong to one campus. |
| DELETE | `/hostel/rooms/{id}/` | Tenant-owned resources that also belong to one campus. |
| GET | `/hostel/rooms/{id}/` | Tenant-owned resources that also belong to one campus. |
| POST | `/hostel/beds/` | Tenant-owned resources that also belong to one campus. |
| GET | `/hostel/beds/` | List beds |
| PATCH | `/hostel/beds/{id}/` | Tenant-owned resources that also belong to one campus. |
| PUT | `/hostel/beds/{id}/` | Tenant-owned resources that also belong to one campus. |
| DELETE | `/hostel/beds/{id}/` | Tenant-owned resources that also belong to one campus. |
| GET | `/hostel/beds/{id}/` | Tenant-owned resources that also belong to one campus. |
| POST | `/hostel/allocations/` | Reserve a bed for a student or staff member |
| GET | `/hostel/allocations/` | List allocations |
| POST | `/hostel/allocations/generate-invoices/` | Bill the term's hostel fees (one invoice per student, safe to rerun) |
| GET | `/hostel/allocations/me/` | My bed (or my children's), current and past |
| GET | `/hostel/allocations/{id}/` | Retrieve an allocation |
| POST | `/hostel/allocations/{id}/cancel/` | Cancel a reservation not yet checked into |
| POST | `/hostel/allocations/{id}/check-in/` | Check in (on or after the reserved date) |
| POST | `/hostel/allocations/{id}/check-out/` | Check out |
| POST | `/hostel/allocations/{id}/move/` | Move to another bed (ends this stay, starts a new one) |
| POST | `/hostel/complaints/` | Record a complaint (office) |
| GET | `/hostel/complaints/` | List complaints |
| POST | `/hostel/complaints/me/` | Raise a complaint about my room |
| GET | `/hostel/complaints/me/` | Complaints I raised |
| GET | `/hostel/complaints/{id}/` | Retrieve a complaint |
| POST | `/hostel/complaints/{id}/assign/` | Hand the complaint to a staff member |
| POST | `/hostel/complaints/{id}/reject/` | Reject (not a valid complaint) |
| POST | `/hostel/complaints/{id}/resolve/` | Mark resolved |

## hr

| Method | Path | What it does |
|---|---|---|
| POST | `/hr/positions/` | Create a position |
| GET | `/hr/positions/` | List positions |
| PATCH | `/hr/positions/{id}/` | Update a position |
| PUT | `/hr/positions/{id}/` | Replace a position |
| DELETE | `/hr/positions/{id}/` | Delete a position |
| GET | `/hr/positions/{id}/` | Retrieve a position |
| POST | `/hr/contracts/` | Create a contract |
| GET | `/hr/contracts/` | List contracts |
| GET | `/hr/contracts/me/` | My contracts |
| PATCH | `/hr/contracts/{id}/` | Update a contract |
| PUT | `/hr/contracts/{id}/` | Replace a contract |
| DELETE | `/hr/contracts/{id}/` | Delete a contract |
| GET | `/hr/contracts/{id}/` | Retrieve a contract |
| POST | `/hr/contracts/{id}/end/` | End a contract on a date |
| POST | `/hr/profiles/` | Create a employee profile |
| GET | `/hr/profiles/` | List employee profiles |
| GET | `/hr/profiles/me/` | My HR profile |
| PATCH | `/hr/profiles/{id}/` | Update a employee profile |
| PUT | `/hr/profiles/{id}/` | Replace a employee profile |
| DELETE | `/hr/profiles/{id}/` | Delete a employee profile |
| GET | `/hr/profiles/{id}/` | Retrieve a employee profile |
| POST | `/hr/documents/` | Create a staff document |
| GET | `/hr/documents/` | List staff documents |
| GET | `/hr/documents/me/` | My documents on file |
| PATCH | `/hr/documents/{id}/` | Update a staff document |
| PUT | `/hr/documents/{id}/` | Replace a staff document |
| DELETE | `/hr/documents/{id}/` | Delete a staff document |
| GET | `/hr/documents/{id}/` | Retrieve a staff document |
| POST | `/hr/fiscal-years/` | Create a fiscal year |
| GET | `/hr/fiscal-years/` | List fiscal years |
| PATCH | `/hr/fiscal-years/{id}/` | Update a fiscal year |
| PUT | `/hr/fiscal-years/{id}/` | Replace a fiscal year |
| DELETE | `/hr/fiscal-years/{id}/` | Delete a fiscal year |
| GET | `/hr/fiscal-years/{id}/` | Retrieve a fiscal year |
| POST | `/hr/leave-types/` | Create a leave type |
| GET | `/hr/leave-types/` | List leave types |
| PATCH | `/hr/leave-types/{id}/` | Update a leave type |
| PUT | `/hr/leave-types/{id}/` | Replace a leave type |
| DELETE | `/hr/leave-types/{id}/` | Delete a leave type |
| GET | `/hr/leave-types/{id}/` | Retrieve a leave type |
| POST | `/hr/leave-balances/` | HR records about one staff member, reaching a campus through them. |
| GET | `/hr/leave-balances/` | List leave balances |
| GET | `/hr/leave-balances/me/` | My leave balances this fiscal year |
| POST | `/hr/leave-balances/open/` | Open a fiscal year's balances for every staff member |
| GET | `/hr/leave-balances/{id}/` | Retrieve a leave balance |
| POST | `/hr/leave-balances/{id}/adjust/` | Add or remove days by hand |
| POST | `/hr/leave-requests/` | Apply for leave on a staff member's behalf |
| GET | `/hr/leave-requests/` | List leave requests |
| POST | `/hr/leave-requests/me/` | Apply for leave |
| GET | `/hr/leave-requests/me/` | My leave requests |
| GET | `/hr/leave-requests/pending/` | Requests waiting for a decision |
| GET | `/hr/leave-requests/{id}/` | Retrieve a leave request |
| POST | `/hr/leave-requests/{id}/approve/` | Approve a leave request |
| POST | `/hr/leave-requests/{id}/cancel/` | Cancel a leave request |
| POST | `/hr/leave-requests/{id}/reject/` | Reject a leave request |

## inventory

| Method | Path | What it does |
|---|---|---|
| POST | `/inventory/categories/` | The default base for tenant-owned resources. |
| GET | `/inventory/categories/` | The default base for tenant-owned resources. |
| PATCH | `/inventory/categories/{id}/` | The default base for tenant-owned resources. |
| PUT | `/inventory/categories/{id}/` | The default base for tenant-owned resources. |
| DELETE | `/inventory/categories/{id}/` | The default base for tenant-owned resources. |
| GET | `/inventory/categories/{id}/` | The default base for tenant-owned resources. |
| POST | `/inventory/suppliers/` | The default base for tenant-owned resources. |
| GET | `/inventory/suppliers/` | The default base for tenant-owned resources. |
| PATCH | `/inventory/suppliers/{id}/` | The default base for tenant-owned resources. |
| PUT | `/inventory/suppliers/{id}/` | The default base for tenant-owned resources. |
| DELETE | `/inventory/suppliers/{id}/` | The default base for tenant-owned resources. |
| GET | `/inventory/suppliers/{id}/` | The default base for tenant-owned resources. |
| POST | `/inventory/items/` | The default base for tenant-owned resources. |
| GET | `/inventory/items/` | The default base for tenant-owned resources. |
| PATCH | `/inventory/items/{id}/` | The default base for tenant-owned resources. |
| PUT | `/inventory/items/{id}/` | The default base for tenant-owned resources. |
| DELETE | `/inventory/items/{id}/` | The default base for tenant-owned resources. |
| GET | `/inventory/items/{id}/` | The default base for tenant-owned resources. |
| POST | `/inventory/stores/` | Tenant-owned resources that also belong to one campus. |
| GET | `/inventory/stores/` | Tenant-owned resources that also belong to one campus. |
| PATCH | `/inventory/stores/{id}/` | Tenant-owned resources that also belong to one campus. |
| PUT | `/inventory/stores/{id}/` | Tenant-owned resources that also belong to one campus. |
| DELETE | `/inventory/stores/{id}/` | Tenant-owned resources that also belong to one campus. |
| GET | `/inventory/stores/{id}/` | Tenant-owned resources that also belong to one campus. |
| GET | `/inventory/stock-levels/` | List/retrieve only — no create route exists at all, so there is nothing |
| POST | `/inventory/stock-levels/adjust/` | Correct stock (stock-take, damage, write-off) |
| GET | `/inventory/stock-levels/{id}/` | List/retrieve only — no create route exists at all, so there is nothing |
| GET | `/inventory/stock-movements/` | List/retrieve only — no create route exists at all, so there is nothing |
| GET | `/inventory/stock-movements/{id}/` | List/retrieve only — no create route exists at all, so there is nothing |
| POST | `/inventory/stock-transfers/` | Move stock between stores |
| GET | `/inventory/stock-transfers/` | Tenant-owned resources that also belong to one campus. |
| GET | `/inventory/stock-transfers/{id}/` | Tenant-owned resources that also belong to one campus. |
| POST | `/inventory/stock-issues/` | Issue consumables to staff or a department |
| GET | `/inventory/stock-issues/` | Tenant-owned resources that also belong to one campus. |
| GET | `/inventory/stock-issues/{id}/` | Tenant-owned resources that also belong to one campus. |
| POST | `/inventory/purchase-orders/` | Draft a purchase order |
| GET | `/inventory/purchase-orders/` | Tenant-owned resources that also belong to one campus. |
| GET | `/inventory/purchase-orders/{id}/` | Tenant-owned resources that also belong to one campus. |
| POST | `/inventory/purchase-orders/{id}/cancel/` | Cancel an order with nothing received |
| POST | `/inventory/purchase-orders/{id}/place/` | Place a draft order with the supplier |
| POST | `/inventory/purchase-orders/{id}/receive/` | Book a delivery (whole or part) |
| POST | `/inventory/assets/` | Register an asset (donation / opening register) |
| GET | `/inventory/assets/` | Tenant-owned resources that also belong to one campus. |
| GET | `/inventory/assets/me/` | Assets currently assigned to me |
| PATCH | `/inventory/assets/{id}/` | Edit an asset's descriptive fields |
| GET | `/inventory/assets/{id}/` | Tenant-owned resources that also belong to one campus. |
| POST | `/inventory/assets/{id}/assign/` | Give the asset to a staff member, student, room or department |
| POST | `/inventory/assets/{id}/dispose/` | Dispose of the asset (final) |
| POST | `/inventory/assets/{id}/move/` | Move the asset to another store (any campus) |
| POST | `/inventory/assets/{id}/return/` | Take the asset back from its holder |
| GET | `/inventory/asset-assignments/` | List/retrieve only — no create route exists at all, so there is nothing |
| GET | `/inventory/asset-assignments/{id}/` | List/retrieve only — no create route exists at all, so there is nothing |
| POST | `/inventory/maintenance/` | Schedule maintenance on an asset |
| GET | `/inventory/maintenance/` | Tenant-owned resources that also belong to one campus. |
| GET | `/inventory/maintenance/{id}/` | Tenant-owned resources that also belong to one campus. |
| POST | `/inventory/maintenance/{id}/cancel/` | Cancel the job |
| POST | `/inventory/maintenance/{id}/complete/` | Finish the job (asset returns to store) |
| POST | `/inventory/maintenance/{id}/start/` | Start the job (asset goes under maintenance) |
| GET | `/inventory/disposals/` | List/retrieve only — no create route exists at all, so there is nothing |
| GET | `/inventory/disposals/{id}/` | List/retrieve only — no create route exists at all, so there is nothing |

## invoices

| Method | Path | What it does |
|---|---|---|
| POST | `/invoices/` | Tenant-owned resources that also belong to one campus. |

## library

| Method | Path | What it does |
|---|---|---|
| POST | `/library/authors/` | The default base for tenant-owned resources. |
| GET | `/library/authors/` | The default base for tenant-owned resources. |
| PATCH | `/library/authors/{id}/` | The default base for tenant-owned resources. |
| PUT | `/library/authors/{id}/` | The default base for tenant-owned resources. |
| DELETE | `/library/authors/{id}/` | The default base for tenant-owned resources. |
| GET | `/library/authors/{id}/` | The default base for tenant-owned resources. |
| POST | `/library/categories/` | The default base for tenant-owned resources. |
| GET | `/library/categories/` | The default base for tenant-owned resources. |
| PATCH | `/library/categories/{id}/` | The default base for tenant-owned resources. |
| PUT | `/library/categories/{id}/` | The default base for tenant-owned resources. |
| DELETE | `/library/categories/{id}/` | The default base for tenant-owned resources. |
| GET | `/library/categories/{id}/` | The default base for tenant-owned resources. |
| POST | `/library/publishers/` | The default base for tenant-owned resources. |
| GET | `/library/publishers/` | The default base for tenant-owned resources. |
| PATCH | `/library/publishers/{id}/` | The default base for tenant-owned resources. |
| PUT | `/library/publishers/{id}/` | The default base for tenant-owned resources. |
| DELETE | `/library/publishers/{id}/` | The default base for tenant-owned resources. |
| GET | `/library/publishers/{id}/` | The default base for tenant-owned resources. |
| POST | `/library/books/` | The default base for tenant-owned resources. |
| GET | `/library/books/` | Browse the catalog |
| PATCH | `/library/books/{id}/` | The default base for tenant-owned resources. |
| PUT | `/library/books/{id}/` | The default base for tenant-owned resources. |
| DELETE | `/library/books/{id}/` | The default base for tenant-owned resources. |
| GET | `/library/books/{id}/` | The default base for tenant-owned resources. |
| POST | `/library/shelves/` | Tenant-owned resources that also belong to one campus. |
| GET | `/library/shelves/` | Tenant-owned resources that also belong to one campus. |
| PATCH | `/library/shelves/{id}/` | Tenant-owned resources that also belong to one campus. |
| PUT | `/library/shelves/{id}/` | Tenant-owned resources that also belong to one campus. |
| DELETE | `/library/shelves/{id}/` | Tenant-owned resources that also belong to one campus. |
| GET | `/library/shelves/{id}/` | Tenant-owned resources that also belong to one campus. |
| POST | `/library/copies/` | Tenant-owned resources that also belong to one campus. |
| GET | `/library/copies/` | Check availability |
| PATCH | `/library/copies/{id}/` | Tenant-owned resources that also belong to one campus. |
| PUT | `/library/copies/{id}/` | Tenant-owned resources that also belong to one campus. |
| DELETE | `/library/copies/{id}/` | Tenant-owned resources that also belong to one campus. |
| GET | `/library/copies/{id}/` | Tenant-owned resources that also belong to one campus. |
| POST | `/library/copies/{id}/withdraw/` | Withdraw the copy from circulation |
| POST | `/library/members/` | Tenant-owned resources that also belong to one campus. |
| GET | `/library/members/` | Tenant-owned resources that also belong to one campus. |
| GET | `/library/members/me/` | My own library membership |
| PATCH | `/library/members/{id}/` | Tenant-owned resources that also belong to one campus. |
| GET | `/library/members/{id}/` | Tenant-owned resources that also belong to one campus. |
| POST | `/library/members/{id}/deactivate/` | Deactivate the membership |
| POST | `/library/issues/` | Issue a copy |
| GET | `/library/issues/` | The default base for tenant-owned resources. |
| GET | `/library/issues/me/` | My own borrowing history |
| GET | `/library/issues/{id}/` | The default base for tenant-owned resources. |
| POST | `/library/issues/{id}/return/` | Return, or report damaged/lost |
| POST | `/library/reservations/` | Reserve a book with nothing available right now |
| GET | `/library/reservations/` | The default base for tenant-owned resources. |
| POST | `/library/reservations/expire-stale/` | Sweep reservations held past their pickup window |
| GET | `/library/reservations/me/` | My own reservations |
| GET | `/library/reservations/{id}/` | The default base for tenant-owned resources. |
| POST | `/library/reservations/{id}/cancel/` | Cancel the reservation |
| POST | `/library/reservations/{id}/fulfil/` | Collect the copy being held (the desk hands it over) |
| POST | `/library/fines/` | The default base for tenant-owned resources. |
| GET | `/library/fines/` | The default base for tenant-owned resources. |
| GET | `/library/fines/me/` | My own fines |
| GET | `/library/fines/{id}/` | The default base for tenant-owned resources. |
| POST | `/library/fines/{id}/pay/` | Record payment of the fine |
| POST | `/library/fines/{id}/waive/` | Waive the fine |

## notices

| Method | Path | What it does |
|---|---|---|
| POST | `/notices/` | Create a notice |
| GET | `/notices/` | List notices |
| PATCH | `/notices/{id}/` | Update a notice |
| PUT | `/notices/{id}/` | Replace a notice |
| DELETE | `/notices/{id}/` | Delete a notice |
| GET | `/notices/{id}/` | Retrieve a notice |
| POST | `/notices/{id}/publish/` | Publish the notice |

## notifications

| Method | Path | What it does |
|---|---|---|
| GET | `/notifications/` | My notifications |
| POST | `/notifications/mark-all-read/` | Mark every notification read |
| GET | `/notifications/unread-count/` | How many are unread |
| GET | `/notifications/{id}/` | One of my notifications |
| POST | `/notifications/{id}/mark-read/` | Mark one notification read |

## organizations

| Method | Path | What it does |
|---|---|---|
| POST | `/organizations/` | Create an organization |
| GET | `/organizations/` | List organizations |
| PATCH | `/organizations/{id}/` | Update an organization |
| PUT | `/organizations/{id}/` | Replace an organization |
| DELETE | `/organizations/{id}/` | Soft-delete an organization |
| GET | `/organizations/{id}/` | Retrieve an organization |
| POST | `/campuses/` | Create a campus |
| GET | `/campuses/` | List campuses |
| PATCH | `/campuses/{id}/` | Update a campus |
| PUT | `/campuses/{id}/` | Replace a campus |
| DELETE | `/campuses/{id}/` | Soft-delete a campus |
| GET | `/campuses/{id}/` | Retrieve a campus |

## parents

| Method | Path | What it does |
|---|---|---|
| POST | `/parents/` | Create a parent |
| GET | `/parents/` | List parents |
| GET | `/parents/me/` | The caller's own parent profile and children |
| PATCH | `/parents/{id}/` | Update a parent |
| PUT | `/parents/{id}/` | Replace a parent |
| DELETE | `/parents/{id}/` | Soft-delete a parent |
| GET | `/parents/{id}/` | Retrieve a parent |
| POST | `/parents/{id}/link-student/` | Link a student to a parent |
| GET | `/parents/{id}/students/` | Students linked to a parent |
| POST | `/parents/{id}/unlink-student/` | Unlink a student from a parent |

## payroll

| Method | Path | What it does |
|---|---|---|
| POST | `/payroll/settings/` | Create a payroll settings row |
| GET | `/payroll/settings/` | List payroll settings rows |
| PATCH | `/payroll/settings/{id}/` | Update a payroll settings row |
| PUT | `/payroll/settings/{id}/` | Replace a payroll settings row |
| DELETE | `/payroll/settings/{id}/` | Delete a payroll settings row |
| GET | `/payroll/settings/{id}/` | Retrieve a payroll settings row |
| POST | `/payroll/components/` | Create a pay component |
| GET | `/payroll/components/` | List pay components |
| PATCH | `/payroll/components/{id}/` | Update a pay component |
| PUT | `/payroll/components/{id}/` | Replace a pay component |
| DELETE | `/payroll/components/{id}/` | Delete a pay component |
| GET | `/payroll/components/{id}/` | Retrieve a pay component |
| POST | `/payroll/structures/` | Create a salary structure |
| GET | `/payroll/structures/` | List salary structures |
| PATCH | `/payroll/structures/{id}/` | Update a salary structure |
| PUT | `/payroll/structures/{id}/` | Replace a salary structure |
| DELETE | `/payroll/structures/{id}/` | Delete a salary structure |
| GET | `/payroll/structures/{id}/` | Retrieve a salary structure |
| POST | `/payroll/staff-salaries/` | Create a staff salary |
| GET | `/payroll/staff-salaries/` | List staff salarys |
| PATCH | `/payroll/staff-salaries/{id}/` | Update a staff salary |
| DELETE | `/payroll/staff-salaries/{id}/` | Delete a staff salary |
| GET | `/payroll/staff-salaries/{id}/` | Retrieve a staff salary |
| POST | `/payroll/tax-schemes/` | Create a tax scheme |
| GET | `/payroll/tax-schemes/` | List tax schemes |
| PATCH | `/payroll/tax-schemes/{id}/` | Update a tax scheme |
| PUT | `/payroll/tax-schemes/{id}/` | Replace a tax scheme |
| DELETE | `/payroll/tax-schemes/{id}/` | Delete a tax scheme |
| GET | `/payroll/tax-schemes/{id}/` | Retrieve a tax scheme |
| POST | `/payroll/runs/` | Create a payroll run |
| GET | `/payroll/runs/` | List payroll runs |
| GET | `/payroll/runs/{id}/` | Retrieve a payroll run |
| POST | `/payroll/runs/{id}/approve/` | Approve a computed run, locking its payslips |
| GET | `/payroll/runs/{id}/bank-sheet/` | Bank transfer list: who gets how much, to which account |
| POST | `/payroll/runs/{id}/cancel/` | Cancel a draft run |
| POST | `/payroll/runs/{id}/compute/` | Work out every payslip (again) |
| POST | `/payroll/runs/{id}/mark-paid/` | Record that an approved run was paid |
| POST | `/payroll/payslips/` | Tenant-owned resources that also belong to one campus. |
| GET | `/payroll/payslips/` | List payslips |
| GET | `/payroll/payslips/me/` | My payslips (approved runs only) |
| GET | `/payroll/payslips/{id}/` | Retrieve a payslip |
| POST | `/payroll/payslips/{id}/set-overtime/` | Set a draft payslip's overtime by hand (null: back to the count) |
| POST | `/payroll/adjustments/` | Create a payroll adjustment |
| GET | `/payroll/adjustments/` | List payroll adjustments |
| DELETE | `/payroll/adjustments/{id}/` | Delete a payroll adjustment |
| GET | `/payroll/adjustments/{id}/` | Retrieve a payroll adjustment |

## permissions

| Method | Path | What it does |
|---|---|---|
| GET | `/permissions/` | List available permissions |
| GET | `/permissions/{id}/` | Retrieve a permission |
| POST | `/roles/` | Create a role |
| GET | `/roles/` | List roles |
| PATCH | `/roles/{id}/` | Update a role |
| PUT | `/roles/{id}/` | Replace a role |
| DELETE | `/roles/{id}/` | Soft-delete a role |
| GET | `/roles/{id}/` | Retrieve a role |

## public applications

| Method | Path | What it does |
|---|---|---|
| GET | `/public/organizations/{code}/application-types/` | Forms open to the public |
| POST | `/public/organizations/{code}/applications/` | Apply (returns your reference and a secret token — keep it) |
| POST | `/public/organizations/{code}/applications/status/` | Check an application |
| POST | `/public/organizations/{code}/applications/resubmit/` | Resubmit after it was sent back |
| POST | `/public/organizations/{code}/applications/withdraw/` | Withdraw an application |

## public careers

| Method | Path | What it does |
|---|---|---|
| GET | `/public/organizations/{code}/careers/vacancies/` | Open vacancies |
| GET | `/public/organizations/{code}/careers/vacancies/{id}/` | One open vacancy |
| POST | `/public/organizations/{code}/careers/vacancies/{id}/apply/` | Apply (returns your reference and a secret token: keep it to check on it) |
| POST | `/public/organizations/{code}/careers/offer/` | See your job offer |
| POST | `/public/organizations/{code}/careers/offer/respond/` | Accept or decline your job offer |

## signup

| Method | Path | What it does |
|---|---|---|
| POST | `/signup/` | Sign up a new organization (emails a link; nothing is created until it's opened) |
| GET | `/signup/config/` | What the signup form needs: open or not, CAPTCHA widget, organization types |
| POST | `/signup/resend/` | Send the verification link again (the old one stops working) |
| POST | `/signup/verify/` | Open the emailed link: creates the organization and signs you in (or queues it for approval) |
| GET | `/signup/check-code/` | Is an organization code free? |
| GET | `/signup-requests/` | Signup requests (platform admins) |
| GET | `/signup-requests/{id}/` | A signup request (platform admins) |
| POST | `/signup-requests/{id}/approve/` | Approve: creates the organization and emails its admin |
| POST | `/signup-requests/{id}/reject/` | Reject (the person is emailed the reason) |

## staff

| Method | Path | What it does |
|---|---|---|
| POST | `/staff/` | Create a staff member |
| GET | `/staff/` | List staff |
| GET | `/staff/me/` | The caller's own staff record |
| PATCH | `/staff/{id}/` | Update a staff member |
| PUT | `/staff/{id}/` | Replace a staff member |
| DELETE | `/staff/{id}/` | Soft-delete a staff member |
| GET | `/staff/{id}/` | Retrieve a staff member |

## students

| Method | Path | What it does |
|---|---|---|
| POST | `/students/` | Create a student (opens their first enrollment) |
| GET | `/students/` | List students |
| GET | `/students/me/` | The caller's own student record |
| PATCH | `/students/{id}/` | Update a student's details |
| PUT | `/students/{id}/` | Replace a student's details |
| DELETE | `/students/{id}/` | Soft-delete a student |
| GET | `/students/{id}/` | Retrieve a student |
| POST | `/students/{id}/change-status/` | Suspend, reactivate, graduate or withdraw a student |
| GET | `/students/{id}/enrollments/` | Enrollment history of a student |
| POST | `/students/{id}/place/` | Place a student in a section, or move them (promotion, new year, section change) |
| POST | `/students/{id}/transfer/` | Transfer a student to another campus |

## support

| Method | Path | What it does |
|---|---|---|
| POST | `/support/tickets/` | Raise a support ticket |
| GET | `/support/tickets/` | List support tickets |
| GET | `/support/tickets/{id}/` | Retrieve a support ticket |
| POST | `/support/tickets/{id}/assign/` | Assign the ticket |
| POST | `/support/tickets/{id}/close/` | Close the ticket |
| POST | `/support/tickets/{id}/comments/` | Comments on the ticket, or add one |
| GET | `/support/tickets/{id}/comments/` | Comments on the ticket, or add one |
| POST | `/support/tickets/{id}/resolve/` | Resolve the ticket |

## system

| Method | Path | What it does |
|---|---|---|
| GET | `/health/` | Liveness probe — the process is up and serving. |
| GET | `/ready/` | Readiness probe — dependencies (database and cache) are reachable. |

## timetable

| Method | Path | What it does |
|---|---|---|
| POST | `/bell-schedules/` | Create a bell schedule |
| GET | `/bell-schedules/` | List bell schedules |
| PATCH | `/bell-schedules/{id}/` | Update a bell schedule |
| PUT | `/bell-schedules/{id}/` | Replace a bell schedule |
| DELETE | `/bell-schedules/{id}/` | Delete a bell schedule |
| GET | `/bell-schedules/{id}/` | Retrieve a bell schedule |
| POST | `/bell-schedules/{id}/retime/` | New bell times from a date (e.g. winter timings) |
| POST | `/periods/` | Create a period |
| GET | `/periods/` | List periods |
| PATCH | `/periods/{id}/` | Update a period |
| PUT | `/periods/{id}/` | Replace a period |
| DELETE | `/periods/{id}/` | Delete a period |
| GET | `/periods/{id}/` | Retrieve a period |
| POST | `/timetable/` | Create a timetable entry |
| GET | `/timetable/` | List timetable entrys |
| GET | `/timetable/day/` | The lessons of one day, with that day's changes |
| POST | `/timetable/generate/` | Generate the weekly timetable |
| POST | `/timetable/hand-over/` | Hand lessons over to another teacher |
| GET | `/timetable/me/` | My timetable |
| PATCH | `/timetable/{id}/` | Update a timetable entry |
| PUT | `/timetable/{id}/` | Replace a timetable entry |
| DELETE | `/timetable/{id}/` | Delete a timetable entry |
| GET | `/timetable/{id}/` | Retrieve a timetable entry |
| POST | `/lesson-changes/` | Create a lesson change |
| GET | `/lesson-changes/` | List lesson changes |
| PATCH | `/lesson-changes/{id}/` | Update a lesson change |
| PUT | `/lesson-changes/{id}/` | Replace a lesson change |
| DELETE | `/lesson-changes/{id}/` | Delete a lesson change |
| GET | `/lesson-changes/{id}/` | Retrieve a lesson change |

## transport

| Method | Path | What it does |
|---|---|---|
| POST | `/transport/vehicles/` | Tenant-owned resources that also belong to one campus. |
| GET | `/transport/vehicles/` | Tenant-owned resources that also belong to one campus. |
| PATCH | `/transport/vehicles/{id}/` | Tenant-owned resources that also belong to one campus. |
| PUT | `/transport/vehicles/{id}/` | Tenant-owned resources that also belong to one campus. |
| DELETE | `/transport/vehicles/{id}/` | Tenant-owned resources that also belong to one campus. |
| GET | `/transport/vehicles/{id}/` | Tenant-owned resources that also belong to one campus. |
| POST | `/transport/vehicle-documents/` | Records about one vehicle, reaching a campus through it. |
| GET | `/transport/vehicle-documents/` | Records about one vehicle, reaching a campus through it. |
| PATCH | `/transport/vehicle-documents/{id}/` | Records about one vehicle, reaching a campus through it. |
| PUT | `/transport/vehicle-documents/{id}/` | Records about one vehicle, reaching a campus through it. |
| DELETE | `/transport/vehicle-documents/{id}/` | Records about one vehicle, reaching a campus through it. |
| GET | `/transport/vehicle-documents/{id}/` | Records about one vehicle, reaching a campus through it. |
| POST | `/transport/drivers/` | Tenant-owned resources that also belong to one campus. |
| GET | `/transport/drivers/` | Tenant-owned resources that also belong to one campus. |
| PATCH | `/transport/drivers/{id}/` | Tenant-owned resources that also belong to one campus. |
| PUT | `/transport/drivers/{id}/` | Tenant-owned resources that also belong to one campus. |
| DELETE | `/transport/drivers/{id}/` | Tenant-owned resources that also belong to one campus. |
| GET | `/transport/drivers/{id}/` | Tenant-owned resources that also belong to one campus. |
| POST | `/transport/routes/` | Tenant-owned resources that also belong to one campus. |
| GET | `/transport/routes/` | Tenant-owned resources that also belong to one campus. |
| PATCH | `/transport/routes/{id}/` | Tenant-owned resources that also belong to one campus. |
| PUT | `/transport/routes/{id}/` | Tenant-owned resources that also belong to one campus. |
| DELETE | `/transport/routes/{id}/` | Tenant-owned resources that also belong to one campus. |
| GET | `/transport/routes/{id}/` | Tenant-owned resources that also belong to one campus. |
| POST | `/transport/stops/` | Tenant-owned resources that also belong to one campus. |
| GET | `/transport/stops/` | Tenant-owned resources that also belong to one campus. |
| PATCH | `/transport/stops/{id}/` | Tenant-owned resources that also belong to one campus. |
| PUT | `/transport/stops/{id}/` | Tenant-owned resources that also belong to one campus. |
| DELETE | `/transport/stops/{id}/` | Tenant-owned resources that also belong to one campus. |
| GET | `/transport/stops/{id}/` | Tenant-owned resources that also belong to one campus. |
| POST | `/transport/assignments/` | Put a student or staff member on a route |
| GET | `/transport/assignments/` | List riders |
| POST | `/transport/assignments/generate-invoices/` | Bill the term's transport fees (one invoice per student, safe to rerun) |
| GET | `/transport/assignments/me/` | My route (or my children's), current and past |
| GET | `/transport/assignments/{id}/` | Retrieve a rider assignment |
| POST | `/transport/assignments/{id}/end/` | Stop riding after a day |
| POST | `/transport/trips/` | Open a trip (crew or office); returns the existing one if open |
| GET | `/transport/trips/` | List trips |
| GET | `/transport/trips/mine/` | Routes I crew, with the day's trips (open them with POST) |
| GET | `/transport/trips/{id}/` | A trip with its roster |
| POST | `/transport/trips/{id}/complete/` | Complete the trip (crew or office) |
| POST | `/transport/trips/{id}/mark/` | Mark who boarded (crew or office) |
| GET | `/transport/trip-records/` | List boarding marks |
| GET | `/transport/trip-records/me/` | My (or my children's) boarding history |
| GET | `/transport/trip-records/{id}/` | Retrieve a boarding mark |
| POST | `/transport/maintenance/` | Records about one vehicle, reaching a campus through it. |
| GET | `/transport/maintenance/` | Records about one vehicle, reaching a campus through it. |
| PATCH | `/transport/maintenance/{id}/` | Records about one vehicle, reaching a campus through it. |
| PUT | `/transport/maintenance/{id}/` | Records about one vehicle, reaching a campus through it. |
| DELETE | `/transport/maintenance/{id}/` | Records about one vehicle, reaching a campus through it. |
| GET | `/transport/maintenance/{id}/` | Records about one vehicle, reaching a campus through it. |
| POST | `/transport/fuel-logs/` | Records about one vehicle, reaching a campus through it. |
| GET | `/transport/fuel-logs/` | Records about one vehicle, reaching a campus through it. |
| PATCH | `/transport/fuel-logs/{id}/` | Records about one vehicle, reaching a campus through it. |
| PUT | `/transport/fuel-logs/{id}/` | Records about one vehicle, reaching a campus through it. |
| DELETE | `/transport/fuel-logs/{id}/` | Records about one vehicle, reaching a campus through it. |
| GET | `/transport/fuel-logs/{id}/` | Records about one vehicle, reaching a campus through it. |

## users

| Method | Path | What it does |
|---|---|---|
| POST | `/users/` | Create a user |
| GET | `/users/` | List users |
| PATCH | `/users/{id}/` | Update a user |
| PUT | `/users/{id}/` | Replace a user |
| DELETE | `/users/{id}/` | Soft-delete a user |
| GET | `/users/{id}/` | Retrieve a user |
| POST | `/users/{id}/assign-role/` | Assign a role to a user |
| POST | `/users/{id}/deactivate/` | Deactivate a user |
| POST | `/users/{id}/reset-2fa/` | Turn off a user's two-factor login (e.g. lost phone) |
| POST | `/users/{id}/revoke-role/` | Revoke a role from a user |
| GET | `/users/{id}/roles/` | List a user's role assignments |
| POST | `/users/{id}/set-password/` | Set a user's password (administrative) |
