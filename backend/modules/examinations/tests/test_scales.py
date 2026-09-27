from ..models import GradeScale
from .base import API, ExamTestCase

SCALES = f"{API}/grades/scales/"


class GradeScaleAPITests(ExamTestCase):
    def setUp(self):
        super().setUp()
        self.scale.delete()           # start each test without the default scale
        self.login(self.office)

    def band(self, minimum, letter="x", point="0", ok=True):
        return {"min_percentage": minimum, "letter": letter, "grade_point": point, "remark": "", "is_pass": ok}

    def test_create_from_a_preset(self):
        response = self.client.post(SCALES, {"name": "NEB", "preset": "neb-style"}, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data["bands"]), 8)
        self.assertEqual(response.data["bands"][0]["letter"], "A+")     # highest first
        self.assertIsNone(response.data["program"])

    def test_create_with_your_own_bands_and_divisions(self):
        response = self.client.post(SCALES, {
            "name": "College", "program": self.program.pk, "max_grade_point": "4.00",
            "bands": [self.band(0, "F", ok=False), self.band(40, "P")],
            "divisions": [{"min_percentage": 60, "name": "First"}, {"min_percentage": 40, "name": "Second"}],
        }, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual([d["name"] for d in response.data["divisions"]], ["First", "Second"])

    def test_needs_bands_or_a_preset(self):
        self.assertEqual(self.client.post(SCALES, {"name": "Empty"}, format="json").status_code, 400)

    def test_a_scale_must_cover_zero_and_have_a_pass(self):
        no_zero = self.client.post(SCALES, {"name": "A", "bands": [self.band(40, "P")]}, format="json")
        no_pass = self.client.post(SCALES, {"name": "B", "bands": [self.band(0, "F", ok=False)]}, format="json")

        self.assertEqual(no_zero.status_code, 400)
        self.assertEqual(no_zero.data["error"]["code"], "scale_invalid")
        self.assertEqual(no_pass.status_code, 400)
        self.assertFalse(GradeScale.objects.exists())        # nothing half-saved

    def test_one_scale_per_program_and_one_default(self):
        self.client.post(SCALES, {"name": "Default", "preset": "neb-style"}, format="json")
        self.client.post(SCALES, {"name": "P", "program": self.program.pk, "preset": "percentage"}, format="json")

        again_default = self.client.post(SCALES, {"name": "Default 2", "preset": "neb-style"}, format="json")
        again_program = self.client.post(SCALES, {"name": "P2", "program": self.program.pk,
                                                  "preset": "percentage"}, format="json")

        self.assertEqual(again_default.status_code, 400)
        self.assertEqual(again_program.status_code, 400)

    def test_replace_bands_on_update(self):
        scale = self.client.post(SCALES, {"name": "S", "preset": "neb-style"}, format="json").data
        response = self.client.patch(f"{SCALES}{scale['id']}/",
                                     {"bands": [self.band(0, "F", ok=False), self.band(50, "P")]}, format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([b["letter"] for b in response.data["bands"]], ["P", "F"])

    def test_renaming_keeps_the_bands(self):
        scale = self.client.post(SCALES, {"name": "S", "preset": "neb-style"}, format="json").data
        response = self.client.patch(f"{SCALES}{scale['id']}/", {"name": "Renamed"}, format="json")

        self.assertEqual(len(response.data["bands"]), 8)

    def test_bands_are_locked_once_results_are_published(self):
        scale = self.make_scale()
        exam = self.make_exam(grade_scale=scale)
        exam.status = "published"
        exam.save(update_fields=["status"])

        response = self.client.patch(f"{SCALES}{scale.pk}/", {"bands": [self.band(0, "P")]}, format="json")

        self.assertError(response, 409, "scale_in_use")
        self.assertTrue(self.client.get(f"{SCALES}{scale.pk}/").data["in_use"])

    def test_cannot_delete_a_scale_exams_use(self):
        scale = self.make_scale()
        self.make_exam(grade_scale=scale)

        self.assertError(self.client.delete(f"{SCALES}{scale.pk}/"), 409, "in_use")

    def test_grade_lookup(self):
        scale = self.make_scale()
        response = self.client.get(f"{SCALES}{scale.pk}/grade/", {"percentage": "82.5"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.data["letter"], response.data["grade_point"], response.data["pass"]),
                         ("A", 3.6, True))
        self.assertEqual(self.client.get(f"{SCALES}{scale.pk}/grade/", {"percentage": "150"}).status_code, 400)
        self.assertEqual(self.client.get(f"{SCALES}{scale.pk}/grade/").status_code, 400)

    def test_a_teacher_can_read_scales_but_not_change_them(self):
        scale = self.make_scale()
        self.login(self.hari)

        self.assertEqual(self.client.get(SCALES).status_code, 200)
        self.assertEqual(self.client.post(SCALES, {"name": "X", "preset": "neb-style"}, format="json").status_code, 403)
        self.assertEqual(self.client.delete(f"{SCALES}{scale.pk}/").status_code, 403)

    def test_a_program_scale_beats_the_default(self):
        default = self.make_scale()
        own = self.make_scale(program=self.program, preset="percentage")
        from ..services import resolve_scale

        self.assertEqual(resolve_scale(self.org.pk, self.program), own)
        own.delete()
        self.assertEqual(resolve_scale(self.org.pk, self.program), default)
