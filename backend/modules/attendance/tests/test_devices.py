"""Biometric devices: the generic API, ZKTeco push, PIN mapping, and gate
punches marking students present."""
from tests.factories import (
    create_campus,
    create_device,
    create_organization,
    create_staff_member,
    create_work_schedule,
    map_pin,
)

from integrations.biometric.zkteco import parse_attlog

from ..models import AttendanceDevice, AttendanceRecord, Punch, StaffAttendanceDay
from .base import API, MONDAY, AttendanceTestCase

PUNCHES = "/api/v1/attendance/device-punches/"


class DeviceTestCase(AttendanceTestCase):
    def setUp(self):
        super().setUp()
        create_work_schedule(self.lalitpur, is_default=True)
        map_pin("1001", staff=self.hari)

    def generic_device(self, **fields):
        self.login(self.office)
        response = self.client.post(f"{API}/devices/", {
            "campus": self.lalitpur.pk, "name": "Staff room", "kind": "generic",
            "serial_number": "GEN-1", **fields})
        self.assertEqual(response.status_code, 201, response.data)
        self.logout()
        return response.data["api_key"]

    def send(self, key, punches, **extra):
        return self.client.post(PUNCHES, {"punches": punches}, HTTP_AUTHORIZATION=f"Device {key}",
                                **extra)


class GenericDeviceTests(DeviceTestCase):
    def test_a_device_sends_punches_with_its_key(self):
        key = self.generic_device()

        response = self.send(key, [{"pin": "1001", "time": f"{MONDAY}T09:55:00", "direction": "in"}])

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {"accepted": 1, "duplicates": 0, "unmapped_pins": []})
        day = StaffAttendanceDay.objects.get(staff=self.hari, date=MONDAY)
        self.assertEqual(day.status, "present")
        self.assertIsNotNone(AttendanceDevice.objects.get().last_seen_at)

    def test_the_key_is_shown_once_and_stored_hashed(self):
        key = self.generic_device()
        device = AttendanceDevice.objects.get()

        self.assertNotIn(key, device.key_hash)
        self.login(self.office)
        self.assertNotIn("api_key", self.client.get(f"{API}/devices/{device.pk}/").data)

    def test_a_resent_batch_is_not_counted_twice(self):
        key = self.generic_device()
        batch = [{"pin": "1001", "time": f"{MONDAY}T09:55:00"}]
        self.send(key, batch)

        response = self.send(key, batch)

        self.assertEqual((response.data["accepted"], response.data["duplicates"]), (0, 1))
        self.assertEqual(Punch.objects.count(), 1)

    def test_a_wrong_key_is_refused(self):
        self.generic_device()

        self.assertEqual(self.send("not-the-key", [{"pin": "1", "time": f"{MONDAY}T09:00:00"}])
                         .status_code, 401)

    def test_a_user_token_cannot_send_punches(self):
        self.login(self.office)

        response = self.client.post(PUNCHES, {"punches": [{"pin": "1001", "time": f"{MONDAY}T09:00:00"}]})

        self.assertIn(response.status_code, (401, 403))
        self.assertFalse(Punch.objects.exists())

    def test_a_disabled_device_is_refused(self):
        key = self.generic_device()
        AttendanceDevice.objects.update(is_active=False)

        self.assertEqual(self.send(key, [{"pin": "1001", "time": f"{MONDAY}T09:00:00"}]).status_code, 401)

    def test_allowed_ips(self):
        key = self.generic_device(allowed_ips=["203.0.113.7"])
        batch = [{"pin": "1001", "time": f"{MONDAY}T09:55:00"}]

        self.assertEqual(self.send(key, batch, REMOTE_ADDR="198.51.100.1").status_code, 401)
        self.assertEqual(self.send(key, batch, REMOTE_ADDR="203.0.113.7").status_code, 200)

    def test_rotating_the_key_retires_the_old_one(self):
        old = self.generic_device()
        self.login(self.office)
        new = self.client.post(f"{API}/devices/{AttendanceDevice.objects.get().pk}/rotate-key/").data["api_key"]
        self.logout()
        batch = [{"pin": "1001", "time": f"{MONDAY}T09:55:00"}]

        self.assertEqual(self.send(old, batch).status_code, 401)
        self.assertEqual(self.send(new, batch).status_code, 200)

    def test_serial_numbers_are_unique_across_organizations(self):
        """A ZKTeco device names itself only by serial, so a second
        organization can't register the same one — or learn whose it is."""
        other = create_organization(code="other")
        create_device(create_campus(other, code="x"), serial_number="ZK-9")
        self.login(self.office)

        response = self.client.post(f"{API}/devices/", {
            "campus": self.lalitpur.pk, "name": "Gate", "kind": "zkteco", "serial_number": "ZK-9"})

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("other", str(response.data).lower())


class MappingTests(DeviceTestCase):
    def test_an_unmapped_pin_is_kept_and_applied_once_mapped(self):
        key = self.generic_device()
        response = self.send(key, [{"pin": "2002", "time": f"{MONDAY}T09:50:00"}])
        self.assertEqual(response.data["unmapped_pins"], ["2002"])
        self.login(self.office)

        mapped = self.client.post(f"{API}/biometric-ids/", {"pin": "2002", "staff": self.sita.pk})

        self.assertEqual(mapped.status_code, 201, mapped.data)
        self.assertEqual(Punch.objects.get().staff, self.sita)
        self.assertTrue(StaffAttendanceDay.objects.filter(staff=self.sita, date=MONDAY).exists())

    def test_a_pin_is_one_person(self):
        self.login(self.office)

        both = self.client.post(f"{API}/biometric-ids/", {"pin": "3", "staff": self.sita.pk,
                                                          "student": self.ram.pk})
        taken = self.client.post(f"{API}/biometric-ids/", {"pin": "1001", "staff": self.sita.pk})

        self.assertEqual((both.status_code, taken.status_code), (400, 400))

    def test_a_device_only_sees_its_own_organizations_pins(self):
        """Org B has a PIN 1001 too; org A's device must not reach B's staff."""
        other = create_organization(code="other")
        their_staff = create_staff_member(create_campus(other, code="x"), employee_number="X-1")
        map_pin("5005", staff=their_staff)
        key = self.generic_device()

        response = self.send(key, [{"pin": "5005", "time": f"{MONDAY}T09:50:00"}])

        self.assertEqual(response.data["unmapped_pins"], ["5005"])
        self.assertIsNone(Punch.objects.get().staff)
        self.assertFalse(StaffAttendanceDay.objects.filter(staff=their_staff).exists())

    def test_a_campus_admin_cannot_map_someone_at_another_campus(self):
        elsewhere = create_staff_member(self.bhaktapur, employee_number="B-1")
        self.login(self.office)

        response = self.client.post(f"{API}/biometric-ids/", {"pin": "77", "staff": elsewhere.pk})

        self.assertEqual(response.status_code, 403)


class ZKTecoTests(DeviceTestCase):
    def setUp(self):
        super().setUp()
        self.device = create_device(self.lalitpur, serial_number="CKJM201260001")

    def push(self, body, serial="CKJM201260001", **extra):
        return self.client.generic("POST", f"/iclock/cdata?SN={serial}&table=ATTLOG&Stamp=9999",
                                   body, content_type="text/plain", **extra)

    def test_handshake(self):
        response = self.client.get("/iclock/cdata", {"SN": "CKJM201260001", "options": "all"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("GET OPTION FROM: CKJM201260001", response.content.decode())

    def test_attendance_log_upload(self):
        response = self.push(f"1001\t{MONDAY} 09:55:12\t0\t1\t0\t0\n"
                             f"1001\t{MONDAY} 17:10:00\t1\t15\t0\t0\n")

        self.assertEqual(response.content.decode(), "OK: 2")
        punches = list(Punch.objects.order_by("punched_at").values_list("direction", "verify"))
        self.assertEqual(punches, [("in", "fingerprint"), ("out", "face")])
        self.assertEqual(StaffAttendanceDay.objects.get(staff=self.hari).worked_minutes, 434)

    def test_a_resent_log_is_ignored(self):
        body = f"1001\t{MONDAY} 09:55:12\t0\t1\t0\t0\n"
        self.push(body)
        self.push(body)

        self.assertEqual(Punch.objects.count(), 1)

    def test_unreadable_lines_are_skipped(self):
        punches, bad = parse_attlog(f"garbage\n1001\t{MONDAY} 09:55:12\t0\t1\n\t\n1001\tnot-a-date\n")

        self.assertEqual((len(punches), bad), (1, 2))

    def test_other_tables_are_acknowledged(self):
        response = self.client.generic("POST", "/iclock/cdata?SN=CKJM201260001&table=OPERLOG",
                                       "OPLOG 4\t0\t2026-09-24 09:00:00", content_type="text/plain")

        self.assertEqual(response.content.decode(), "OK")
        self.assertFalse(Punch.objects.exists())

    def test_an_unknown_device_is_refused(self):
        response = self.push(f"1001\t{MONDAY} 09:55:12\t0\t1\n", serial="NOT-OURS")

        self.assertEqual(response.status_code, 401)
        self.assertFalse(Punch.objects.exists())

    def test_allowed_ips_pin_the_device_to_the_school(self):
        self.device.allowed_ips = ["203.0.113.7"]
        self.device.save()

        self.assertEqual(self.push(f"1001\t{MONDAY} 09:55:12\t0\t1\n", REMOTE_ADDR="198.51.100.9")
                         .status_code, 401)
        self.assertEqual(self.push(f"1001\t{MONDAY} 09:55:12\t0\t1\n", REMOTE_ADDR="203.0.113.7")
                         .status_code, 200)

    def test_polling_for_commands(self):
        response = self.client.get("/iclock/getrequest", {"SN": "CKJM201260001"})

        self.assertEqual(response.content.decode(), "OK")


class StudentGateTests(DeviceTestCase):
    def setUp(self):
        super().setUp()
        self.device = create_device(self.lalitpur, serial_number="GATE-1")
        map_pin("5001", student=self.anu)
        map_pin("5002", student=self.ram)

    def gate(self, pin, hhmm="09:40"):
        return self.client.generic("POST", "/iclock/cdata?SN=GATE-1&table=ATTLOG",
                                   f"{pin}\t{MONDAY} {hhmm}:00\t0\t1\n", content_type="text/plain")

    def test_a_gate_punch_marks_the_roll_call_present(self):
        self.gate("5001")

        record = AttendanceRecord.objects.get(enrollment__student=self.anu)
        self.assertEqual((record.status, record.source), ("present", "biometric"))
        self.assertEqual(record.session.kind, "daily")

    def test_a_teachers_mark_wins(self):
        self.login(self.gita)
        session = self.open_daily(self.grade5).data["id"]
        self.mark(session, [(self.enrollment(self.anu).pk, "absent")])

        self.gate("5001")

        self.assertEqual(AttendanceRecord.objects.get(enrollment__student=self.anu).status, "absent")

    def test_a_submitted_roll_call_is_not_changed(self):
        self.login(self.gita)
        session = self.open_daily(self.grade5).data["id"]
        self.client.post(f"{API}/sessions/{session}/submit/", {"rest": "absent"})

        self.gate("5001")

        self.assertEqual(AttendanceRecord.objects.get(enrollment__student=self.anu).status, "absent")

    def test_lesson_attendance_is_not_taken_at_the_gate(self):
        """Being at the gate isn't being in Physics."""
        self.gate("5002")

        self.assertFalse(AttendanceRecord.objects.filter(enrollment__student=self.ram).exists())
        self.assertEqual(Punch.objects.get().student, self.ram)
