"""Point rules earning points automatically, and awards granted from them."""
from ..models import (
    Award,
    AwardRule,
    EventCategory,
    PointRule,
    StudentAward,
    StudentPoints,
)
from .base import API, STARTED, EventTestCase

EVENTS = f"{API}/events/"
POINT_RULES = f"{API}/point-rules/"
POINT_ENTRIES = f"{API}/point-entries/"
AWARDS = f"{API}/awards/"
AWARD_RULES = f"{API}/award-rules/"
STUDENT_AWARDS = f"{API}/student-awards/"
STUDENT_POINTS = f"{API}/student-points/"


class PointRuleTests(EventTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.office)

    def test_create_an_attendance_rule(self):
        r = self.client.post(POINT_RULES, {"name": "Attend sports", "source": "attendance", "points": "10",
                                           "category": self.category.pk})
        self.assertEqual(r.status_code, 201, r.data)

    def test_a_role_needs_a_participation_rule(self):
        r = self.client.post(POINT_RULES, {"name": "x", "source": "attendance", "role": "winner", "points": "10"})
        self.assertEqual(r.status_code, 400)

    def test_points_must_be_positive(self):
        r = self.client.post(POINT_RULES, {"name": "x", "source": "attendance", "points": "0"})
        self.assertEqual(r.status_code, 400)


class AttendancePointsTests(EventTestCase):
    def setUp(self):
        super().setUp()
        self.event = self.make_event(start_at=STARTED)
        PointRule.objects.create(organization=self.org, name="Attend", category=self.category, source="attendance",
                                 points=10)

    def test_marking_present_awards_points(self):
        self.login(self.hari_user)
        self.client.post(f"{EVENTS}{self.event.pk}/mark-attendance/",
                         {"entries": [{"student": self.ram.pk, "status": "present"}]}, format="json")
        totals = StudentPoints.objects.get(student=self.ram)
        self.assertEqual(totals.total, 10)

    def test_remarking_present_does_not_award_again(self):
        self.login(self.hari_user)
        entries = {"entries": [{"student": self.ram.pk, "status": "present"}]}
        self.client.post(f"{EVENTS}{self.event.pk}/mark-attendance/", entries, format="json")
        self.client.post(f"{EVENTS}{self.event.pk}/mark-attendance/", entries, format="json")
        self.assertEqual(StudentPoints.objects.get(student=self.ram).total, 10)

    def test_marking_absent_awards_nothing(self):
        self.login(self.hari_user)
        self.client.post(f"{EVENTS}{self.event.pk}/mark-attendance/",
                         {"entries": [{"student": self.ram.pk, "status": "absent"}]}, format="json")
        self.assertFalse(StudentPoints.objects.filter(student=self.ram).exists())

    def test_a_rule_for_a_different_category_does_not_apply(self):
        other_category = EventCategory.objects.create(organization=self.org, code="cultural", name="Cultural")
        event = self.make_event(start_at=STARTED, name="Debate", category=other_category)
        self.login(self.hari_user)
        self.client.post(f"{EVENTS}{event.pk}/mark-attendance/",
                         {"entries": [{"student": self.ram.pk, "status": "present"}]}, format="json")
        self.assertFalse(StudentPoints.objects.filter(student=self.ram).exists())

    def test_a_category_wide_rule_applies_to_every_category(self):
        PointRule.objects.create(organization=self.org, name="Attend anything", source="attendance", points=5)
        other_category = EventCategory.objects.create(organization=self.org, code="cultural", name="Cultural")
        event = self.make_event(start_at=STARTED, name="Debate", category=other_category)
        self.login(self.hari_user)
        self.client.post(f"{EVENTS}{event.pk}/mark-attendance/",
                         {"entries": [{"student": self.ram.pk, "status": "present"}]}, format="json")
        self.assertEqual(StudentPoints.objects.get(student=self.ram).total, 5)


class ParticipationPointsTests(EventTestCase):
    def setUp(self):
        super().setUp()
        self.event = self.make_event(start_at=STARTED)
        PointRule.objects.create(organization=self.org, name="Win", category=self.category, source="participation",
                                 role="winner", points=50)

    def test_winning_awards_points(self):
        self.login(self.hari_user)
        self.client.post(f"{EVENTS}{self.event.pk}/record-participation/",
                         {"student": self.ram.pk, "role": "winner"})
        self.assertEqual(StudentPoints.objects.get(student=self.ram).total, 50)

    def test_a_different_role_is_not_matched(self):
        self.login(self.hari_user)
        self.client.post(f"{EVENTS}{self.event.pk}/record-participation/",
                         {"student": self.ram.pk, "role": "participant"})
        self.assertFalse(StudentPoints.objects.filter(student=self.ram).exists())

    def test_re_recording_the_same_role_does_not_award_twice(self):
        self.login(self.hari_user)
        self.client.post(f"{EVENTS}{self.event.pk}/record-participation/",
                         {"student": self.ram.pk, "role": "winner", "position": 1})
        self.client.post(f"{EVENTS}{self.event.pk}/record-participation/",
                         {"student": self.ram.pk, "role": "winner", "position": 2})
        self.assertEqual(StudentPoints.objects.get(student=self.ram).total, 50)


class ManualPointsTests(EventTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.office)

    def test_award_points_by_hand(self):
        r = self.client.post(POINT_ENTRIES, {"student": self.ram.pk, "points": "20", "reason": "Sportsmanship"})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(StudentPoints.objects.get(student=self.ram).total, 20)

    def test_negative_points_are_a_deduction(self):
        self.client.post(POINT_ENTRIES, {"student": self.ram.pk, "points": "20", "reason": "x"})
        self.client.post(POINT_ENTRIES, {"student": self.ram.pk, "points": "-5", "reason": "Late arrival"})
        self.assertEqual(StudentPoints.objects.get(student=self.ram).total, 15)

    def test_zero_points_is_refused(self):
        r = self.client.post(POINT_ENTRIES, {"student": self.ram.pk, "points": "0", "reason": "x"})
        self.assertEqual(r.status_code, 400)

    def test_a_teacher_cannot_award_points_by_hand(self):
        self.login(self.hari_user)
        r = self.client.post(POINT_ENTRIES, {"student": self.ram.pk, "points": "10", "reason": "x"})
        self.assertEqual(r.status_code, 403)

    def test_leaderboard_ranks_by_points(self):
        self.client.post(POINT_ENTRIES, {"student": self.ram.pk, "points": "30", "reason": "x"})
        self.client.post(POINT_ENTRIES, {"student": self.shyam.pk, "points": "50", "reason": "x"})
        r = self.client.get(f"{STUDENT_POINTS}leaderboard/")
        self.assertEqual([row["student"] for row in r.data], [self.shyam.pk, self.ram.pk])
        self.assertEqual(r.data[0]["rank"], 1)


class AwardTests(EventTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.office)
        self.badge = Award.objects.create(organization=self.org, kind="badge", code="gold-star", name="Gold Star")

    def test_manual_grant_and_end(self):
        r = self.client.post(STUDENT_AWARDS, {"student": self.ram.pk, "award": self.badge.pk, "note": "Well done"})
        self.assertEqual(r.status_code, 201, r.data)
        grant_id = r.data["id"]
        self.assertEqual(self.client.post(STUDENT_AWARDS,
                         {"student": self.ram.pk, "award": self.badge.pk}).status_code, 409)
        r2 = self.client.post(f"{STUDENT_AWARDS}{grant_id}/end/")
        self.assertEqual(r2.status_code, 200, r2.data)
        self.assertIsNotNone(r2.data["ended_on"])
        # Ended: can be granted again.
        self.assertEqual(self.client.post(STUDENT_AWARDS,
                         {"student": self.ram.pk, "award": self.badge.pk}).status_code, 201)

    def test_cannot_delete_an_award_thats_held(self):
        self.client.post(STUDENT_AWARDS, {"student": self.ram.pk, "award": self.badge.pk})
        self.assertError(self.client.delete(f"{AWARDS}{self.badge.pk}/"), 409, "in_use")

    def test_a_teacher_cannot_grant_awards(self):
        self.login(self.hari_user)
        r = self.client.post(STUDENT_AWARDS, {"student": self.ram.pk, "award": self.badge.pk})
        self.assertEqual(r.status_code, 403)


class AutomaticAwardTests(EventTestCase):
    def setUp(self):
        super().setUp()
        self.badge = Award.objects.create(organization=self.org, kind="achievement", code="century", name="Century")
        self.rule = AwardRule.objects.create(organization=self.org, award=self.badge, threshold_kind="points_total",
                                             threshold_value=100)

    def test_crossing_the_threshold_grants_it(self):
        from modules.events import services

        services.award_points(self.ram, 60, "First event")
        self.assertFalse(StudentAward.objects.filter(student=self.ram, award=self.badge).exists())
        services.award_points(self.ram, 50, "Second event")
        self.assertTrue(StudentAward.objects.filter(student=self.ram, award=self.badge,
                                                     ended_on__isnull=True).exists())

    def test_it_is_only_granted_once(self):
        from modules.events import services

        services.award_points(self.ram, 150, "Big win")
        services.award_points(self.ram, 10, "More")
        self.assertEqual(StudentAward.objects.filter(student=self.ram, award=self.badge).count(), 1)

    def test_an_events_attended_rule_scoped_to_a_category(self):
        from modules.events import services

        badge = Award.objects.create(organization=self.org, kind="badge", code="regular", name="Regular")
        AwardRule.objects.create(organization=self.org, award=badge, threshold_kind="events_attended",
                                 threshold_value=2, category=self.category)
        other_category = EventCategory.objects.create(organization=self.org, code="cultural", name="Cultural")
        e1 = self.make_event(start_at=STARTED, name="E1")
        e2 = self.make_event(start_at=STARTED, name="E2")
        e3 = self.make_event(start_at=STARTED, name="E3", category=other_category)
        services.mark_attendance(e1, [(self.ram.pk, "present")], by=self.office)
        services.mark_attendance(e3, [(self.ram.pk, "present")], by=self.office)
        self.assertFalse(StudentAward.objects.filter(student=self.ram, award=badge).exists())
        services.mark_attendance(e2, [(self.ram.pk, "present")], by=self.office)
        self.assertTrue(StudentAward.objects.filter(student=self.ram, award=badge).exists())

    def test_an_events_won_rule(self):
        from modules.events import services

        badge = Award.objects.create(organization=self.org, kind="title", code="champion", name="Champion")
        AwardRule.objects.create(organization=self.org, award=badge, threshold_kind="events_won", threshold_value=1)
        event = self.make_event(start_at=STARTED)
        services.record_participation(event, self.ram, "winner", by=self.office)
        self.assertTrue(StudentAward.objects.filter(student=self.ram, award=badge).exists())

    def test_my_summary_shows_points_and_awards(self):
        from modules.events import services
        from tests.factories import create_user

        services.award_points(self.ram, 150, "Big win")
        user = create_user(self.org, email="ram@kmc.test")
        self.ram.user = user
        self.ram.save(update_fields=["user"])
        self.login(user)
        r = self.client.get(f"{API}/student-points/me/")
        self.assertEqual(r.data["points"], 150)
        self.assertEqual(r.data["awards"][0]["name"], "Century")
