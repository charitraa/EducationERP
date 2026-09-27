"""The grading rules on their own: no database, no requests."""
from decimal import Decimal as D

from django.test import SimpleTestCase

from core.common.exceptions import ServiceError

from .. import grading, services
from ..grading import Band, ComponentInput, Scale


def scale(preset="neb-style", **kw):
    bands, divisions = services.preset_bands(preset)
    return Scale(
        tuple(Band(b["min_percentage"], b["letter"], b["grade_point"], b["remark"], b["is_pass"])
              for b in sorted(bands, key=lambda b: -b["min_percentage"])),
        tuple((d["min_percentage"], d["name"]) for d in sorted(divisions, key=lambda d: -d["min_percentage"])),
        **kw)


def component(marks, status="present", full=100, pass_marks=35, name="Theory"):
    return ComponentInput(name, "theory", D(full), D(pass_marks), status, None if marks is None else D(marks))


def subject(sc, *components, credit=4):
    return grading.grade_subject(sc, subject_id=1, exam_subject_id=1, credit=credit, components=list(components))


class PercentAndRoundingTests(SimpleTestCase):
    def test_percentage_rounds_half_up_to_two_places(self):
        self.assertEqual(grading.percent(D("2"), D("3")), D("66.67"))
        self.assertEqual(grading.percent(D("1"), D("8")), D("12.50"))
        self.assertEqual(grading.percent(D("1"), D("16")), D("6.25"))
        self.assertEqual(grading.percent(D("5"), D("16") * 100 / 100 * 32), D("0.98"))  # 0.9765625 -> 0.98

    def test_zero_full_marks_is_zero_not_an_error(self):
        self.assertEqual(grading.percent(D("0"), D("0")), D("0"))


class BandLookupTests(SimpleTestCase):
    def test_a_percentage_gets_the_highest_band_at_or_below_it(self):
        sc = scale()
        for pct, letter in [("100", "A+"), ("90", "A+"), ("89.99", "A"), ("35", "D"), ("34.99", "NG"), ("0", "NG")]:
            self.assertEqual(sc.band_for(D(pct)).letter, letter, pct)

    def test_a_scale_without_a_zero_band_refuses_low_marks(self):
        sc = Scale((Band(D("40"), "P", D("0"), "", True),), ())
        with self.assertRaises(ServiceError):
            sc.band_for(D("10"))

    def test_division_by_overall_percentage(self):
        sc = scale("percentage")
        self.assertEqual(sc.division_for(D("81")), "Distinction")
        self.assertEqual(sc.division_for(D("60")), "First division")
        self.assertEqual(sc.division_for(D("36")), "Third division")
        self.assertEqual(sc.division_for(D("20")), "")


class ScaleValidationTests(SimpleTestCase):
    def band(self, minimum, ok=True):
        return {"min_percentage": D(minimum), "letter": "x", "grade_point": D("0"), "remark": "", "is_pass": ok}

    def test_a_good_scale_has_no_problems(self):
        self.assertEqual(grading.validate_bands([self.band(0, False), self.band(35)], []), [])

    def test_needs_a_band_at_zero(self):
        self.assertTrue(any("0%" in p for p in grading.validate_bands([self.band(35)], [])))

    def test_needs_a_pass_band(self):
        self.assertTrue(any("pass" in p for p in grading.validate_bands([self.band(0, False)], [])))

    def test_no_empty_scale_and_no_duplicate_starts(self):
        self.assertTrue(grading.validate_bands([], []))
        self.assertTrue(grading.validate_bands([self.band(0), self.band(0)], []))

    def test_a_higher_band_cannot_fail_when_a_lower_one_passes(self):
        problems = grading.validate_bands([self.band(0, True), self.band(50, False)], [])
        self.assertTrue(any("higher" in p for p in problems))


class SubjectTests(SimpleTestCase):
    def setUp(self):
        self.sc = scale()

    def test_a_pass(self):
        out = subject(self.sc, component(75))
        self.assertEqual((out.status, out.percentage, out.band.letter), ("pass", D("75.00"), "B+"))

    def test_below_the_pass_mark_fails_even_if_the_band_would_pass(self):
        out = subject(self.sc, component(36, pass_marks=40))       # 36% is band D (pass) but under 40
        self.assertEqual(out.status, "fail")

    def test_each_component_needs_its_own_pass_mark(self):
        theory = component(70, full=75, pass_marks=27, name="Theory")   # 93%
        practical = component(10, full=25, pass_marks=10, name="Practical")
        self.assertEqual(subject(self.sc, theory, practical).status, "pass")
        weak_practical = component(9, full=25, pass_marks=10, name="Practical")
        out = subject(self.sc, theory, weak_practical)
        self.assertEqual(out.status, "fail")     # 79% overall, but failed practical
        self.assertEqual(out.percentage, D("79.00"))

    def test_absent_in_every_component_is_zero_and_flagged(self):
        out = subject(self.sc, component(None, "absent"))
        self.assertEqual((out.status, out.absent, out.percentage), ("fail", True, D("0.00")))

    def test_absent_in_one_component_counts_zero_for_it(self):
        out = subject(self.sc, component(60, full=75, pass_marks=27), component(None, "absent", full=25, pass_marks=10))
        self.assertEqual((out.status, out.absent, out.obtained), ("fail", False, D("60")))

    def test_exempt_component_is_left_out_of_the_maths(self):
        out = subject(self.sc, component(60, full=75, pass_marks=27), component(None, "exempt", full=25))
        self.assertEqual((out.full, out.percentage, out.status), (D("75"), D("80.00"), "pass"))

    def test_all_exempt_means_the_subject_is_skipped(self):
        self.assertEqual(subject(self.sc, component(None, "exempt")).status, "exempt")

    def test_a_missing_mark_makes_it_incomplete(self):
        self.assertEqual(subject(self.sc, component(None, None)).status, "incomplete")

    def test_withheld_holds_the_subject_back(self):
        self.assertEqual(subject(self.sc, component(None, "withheld")).status, "withheld")


class OverallTests(SimpleTestCase):
    def setUp(self):
        self.sc = scale()

    def outcomes(self, *pairs):
        """(marks, credit) pairs, each a one-component paper."""
        return [subject(self.sc, component(m), credit=c) for m, c in pairs]

    def test_gpa_is_weighted_by_credit_hours(self):
        # 90% (4.0) at 4 credits and 50% (2.4) at 2 credits: (16 + 4.8) / 6 = 3.4667 -> 3.47
        overall = grading.combine(self.sc, self.outcomes((90, 4), (50, 2)))
        self.assertEqual(overall.grade_point, D("3.47"))
        self.assertEqual(overall.letter, "B+")           # highest band whose point is <= 3.47 is 3.2
        self.assertEqual(overall.status, "pass")

    def test_totals_and_percentage(self):
        overall = grading.combine(self.sc, self.outcomes((80, 4), (60, 4)))
        self.assertEqual((overall.total_obtained, overall.total_full, overall.percentage),
                         (D("140"), D("200"), D("70.00")))

    def test_one_failed_subject_fails_the_result_by_default(self):
        self.assertEqual(grading.combine(self.sc, self.outcomes((90, 4), (20, 4))).status, "fail")

    def test_a_scale_can_allow_failing_a_subject(self):
        lenient = scale(require_all_subjects_pass=False)
        outs = [subject(lenient, component(m)) for m in (90, 20)]
        self.assertEqual(grading.combine(lenient, outs).status, "pass")

    def test_overall_pass_percentage_can_fail_a_result(self):
        strict = scale(overall_pass_percentage=D("50"), require_all_subjects_pass=False)
        outs = [subject(strict, component(m)) for m in (60, 36)]        # 48% overall
        self.assertEqual(grading.combine(strict, outs).status, "fail")

    def test_division_only_for_a_pass(self):
        sc = scale("percentage")
        good = grading.combine(sc, [subject(sc, component(85))])
        bad = grading.combine(sc, [subject(sc, component(20))])
        self.assertEqual((good.division, good.status), ("Distinction", "pass"))
        self.assertEqual((bad.division, bad.status), ("", "fail"))

    def test_incomplete_and_withheld_come_before_pass_or_fail(self):
        incomplete = [subject(self.sc, component(90)), subject(self.sc, component(None, None))]
        withheld = [subject(self.sc, component(90)), subject(self.sc, component(None, "withheld"))]
        self.assertEqual(grading.combine(self.sc, incomplete).status, "incomplete")
        self.assertEqual(grading.combine(self.sc, withheld).status, "withheld")
        self.assertEqual(grading.combine(self.sc, withheld).letter, "")

    def test_no_subjects_is_incomplete_and_exempt_ones_dont_count(self):
        self.assertEqual(grading.combine(self.sc, []).status, "incomplete")
        outs = [subject(self.sc, component(80)), subject(self.sc, component(None, "exempt"))]
        overall = grading.combine(self.sc, outs)
        self.assertEqual((overall.status, overall.percentage), ("pass", D("80.00")))

    def test_percentage_scales_grade_by_percentage(self):
        sc = scale("percentage")
        overall = grading.combine(sc, [subject(sc, component(72))])
        self.assertEqual(overall.letter, "Pass")


class WeightedTests(SimpleTestCase):
    def setUp(self):
        self.sc = scale()

    def part(self, weight, marks, exam=1):
        return (D(weight), {"exam": exam, "name": f"E{exam}"}, subject(self.sc, component(marks)))

    def combine(self, *parts):
        return grading.combine_weighted(self.sc, list(parts), subject_id=1, credit=4)

    def test_weights_apply_to_each_exams_percentage(self):
        out = self.combine(self.part(20, 50, 1), self.part(80, 90, 2))   # 10 + 72
        self.assertEqual((out.percentage, out.status, out.band.letter), (D("82.00"), "pass", "A"))
        self.assertEqual(len(out.detail), 2)

    def test_an_exam_without_the_subject_is_skipped_and_weights_fill_the_gap(self):
        out = self.combine((D(20), {"exam": 1, "name": "E1"}, None), self.part(80, 60, 2))
        self.assertEqual(out.percentage, D("60.00"))

    def test_absent_counts_zero_at_full_weight(self):
        absent = (D(20), {"exam": 1, "name": "E1"}, subject(self.sc, component(None, "absent")))
        out = self.combine(absent, self.part(80, 100, 2))
        self.assertEqual(out.percentage, D("80.00"))

    def test_exempt_exam_is_skipped(self):
        exempt = (D(20), {"exam": 1, "name": "E1"}, subject(self.sc, component(None, "exempt")))
        self.assertEqual(self.combine(exempt, self.part(80, 70, 2)).percentage, D("70.00"))

    def test_an_unfinished_or_withheld_part_holds_the_subject_back(self):
        pending = (D(20), {"exam": 1, "name": "E1"}, subject(self.sc, component(None, None)))
        withheld = (D(20), {"exam": 1, "name": "E1"}, subject(self.sc, component(None, "withheld")))
        self.assertEqual(self.combine(pending, self.part(80, 70, 2)).status, "incomplete")
        self.assertEqual(self.combine(withheld, self.part(80, 70, 2)).status, "withheld")

    def test_nothing_to_combine_is_exempt(self):
        self.assertEqual(self.combine((D(100), {"exam": 1, "name": "E1"}, None)).status, "exempt")

    def test_a_low_weighted_total_fails_the_subject(self):
        self.assertEqual(self.combine(self.part(20, 100, 1), self.part(80, 10, 2)).status, "fail")   # 28%


class RankTests(SimpleTestCase):
    def test_competition_ranking_shares_places_and_skips(self):
        ranks = grading.rank([(1, D("90")), (2, D("85")), (3, D("85")), (4, D("70"))])
        self.assertEqual(ranks, {1: 1, 2: 2, 3: 2, 4: 4})

    def test_empty(self):
        self.assertEqual(grading.rank([]), {})
