"""How marks become grades. Pure functions: no database, no requests.

Rounding is always half-up to two places, at the same points every time
(a subject's percentage, then the overall one), so a result is the same
whoever recomputes it.
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from core.common.exceptions import ServiceError

ZERO = Decimal("0")
HUNDRED = Decimal("100")
CENTS = Decimal("0.01")


def q2(value) -> Decimal:
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def percent(obtained, full) -> Decimal:
    if not full:
        return ZERO
    return q2(Decimal(obtained) * HUNDRED / Decimal(full))


@dataclass(frozen=True)
class Band:
    min_percentage: Decimal
    letter: str
    grade_point: Decimal
    remark: str
    is_pass: bool


@dataclass(frozen=True)
class Scale:
    bands: tuple[Band, ...]                       # highest first
    divisions: tuple[tuple[Decimal, str], ...]    # highest first
    require_all_subjects_pass: bool = True
    overall_pass_percentage: Decimal | None = None

    def band_for(self, pct: Decimal) -> Band:
        for band in self.bands:
            if pct >= band.min_percentage:
                return band
        raise ServiceError("The grade scale has no band starting at 0%.", code="scale_incomplete")

    def division_for(self, pct: Decimal) -> str:
        for minimum, name in self.divisions:
            if pct >= minimum:
                return name
        return ""

    def letter_for_gpa(self, gpa: Decimal, pct: Decimal) -> str:
        """The overall letter: by grade point when the scale has points,
        otherwise by percentage."""
        if any(b.grade_point > 0 for b in self.bands):
            eligible = [b for b in self.bands if b.grade_point <= gpa]
            if eligible:
                return max(eligible, key=lambda b: b.grade_point).letter
            return min(self.bands, key=lambda b: b.grade_point).letter
        return self.band_for(pct).letter


def load_scale(grade_scale) -> Scale:
    """A ``GradeScale`` row as plain data."""
    bands = tuple(Band(b.min_percentage, b.letter, b.grade_point, b.remark, b.is_pass)
                  for b in sorted(grade_scale.bands.all(), key=lambda b: -b.min_percentage))
    divisions = tuple((d.min_percentage, d.name)
                      for d in sorted(grade_scale.divisions.all(), key=lambda d: -d.min_percentage))
    return Scale(bands, divisions, grade_scale.require_all_subjects_pass, grade_scale.overall_pass_percentage)


def validate_bands(bands: list[dict], divisions: list[dict]) -> list[str]:
    """Problems with a scale's bands, in words. Empty when it's usable."""
    problems = []
    if not bands:
        return ["Give at least one grade band."]
    minimums = [b["min_percentage"] for b in bands]
    if len(set(minimums)) != len(minimums):
        problems.append("Two grade bands start at the same percentage.")
    if ZERO not in minimums:
        problems.append("One grade band must start at 0%, or low marks have no grade.")
    # ``is_pass`` is optional in the API: the model defaults it to a pass.
    bands = [{"is_pass": True, **b} for b in bands]
    if not any(b["is_pass"] for b in bands):
        problems.append("At least one grade band must be a pass.")
    ordered = sorted(bands, key=lambda b: b["min_percentage"])
    for lower, higher in zip(ordered, ordered[1:]):
        if higher["is_pass"] is False and lower["is_pass"] is True:
            problems.append("A higher grade band can't fail when a lower one passes.")
            break
    division_minimums = [d["min_percentage"] for d in divisions]
    if len(set(division_minimums)) != len(division_minimums):
        problems.append("Two divisions start at the same percentage.")
    return problems


# ---------------------------------------------------------------------------
# One subject
# ---------------------------------------------------------------------------
@dataclass
class ComponentInput:
    name: str
    kind: str
    full: Decimal
    pass_marks: Decimal
    status: str | None        # None: no mark entered yet
    marks: Decimal | None


@dataclass
class SubjectOutcome:
    subject_id: int
    exam_subject_id: int | None
    credit: Decimal
    obtained: Decimal = ZERO
    full: Decimal = ZERO
    percentage: Decimal = ZERO
    band: Band | None = None
    status: str = "pass"          # pass | fail | withheld | incomplete | exempt
    absent: bool = False
    detail: list = field(default_factory=list)

    @property
    def counts(self) -> bool:
        """Does this line take part in totals and the GPA?"""
        return self.status in ("pass", "fail")


def grade_subject(scale: Scale, *, subject_id, exam_subject_id, credit, components: list[ComponentInput]) -> SubjectOutcome:
    out = SubjectOutcome(subject_id, exam_subject_id, Decimal(credit))
    active = [c for c in components if c.status != "exempt"]
    for c in components:
        out.detail.append({"component": c.name, "kind": c.kind, "full": str(c.full),
                           "pass_marks": str(c.pass_marks), "status": c.status,
                           "marks": None if c.marks is None else str(c.marks)})
    if not active:
        out.status = "exempt"
        return out
    if any(c.status is None for c in active):
        out.status = "incomplete"
        return out
    if any(c.status == "withheld" for c in active):
        out.status = "withheld"
        return out

    out.full = sum((c.full for c in active), ZERO)
    out.obtained = sum((c.marks or ZERO for c in active), ZERO)
    out.absent = all(c.status == "absent" for c in active)
    out.percentage = percent(out.obtained, out.full)
    out.band = scale.band_for(out.percentage)
    components_pass = all(c.status == "present" and c.marks >= c.pass_marks for c in active)
    out.status = "pass" if out.band.is_pass and components_pass else "fail"
    return out


# ---------------------------------------------------------------------------
# A whole result
# ---------------------------------------------------------------------------
@dataclass
class Overall:
    total_obtained: Decimal
    total_full: Decimal
    percentage: Decimal
    grade_point: Decimal
    letter: str
    division: str
    status: str


def combine(scale: Scale, outcomes: list[SubjectOutcome]) -> Overall:
    """Totals, GPA and pass/fail over a student's subjects."""
    lines = [o for o in outcomes if o.status != "exempt"]
    counted = [o for o in lines if o.counts]
    total_obtained = sum((o.obtained for o in counted), ZERO)
    total_full = sum((o.full for o in counted), ZERO)
    pct = percent(total_obtained, total_full)
    credits = sum((o.credit for o in counted), ZERO)
    gpa = q2(sum((o.band.grade_point * o.credit for o in counted), ZERO) / credits) if credits else ZERO

    if not lines or any(o.status == "incomplete" for o in lines):
        status = "incomplete"
    elif any(o.status == "withheld" for o in lines):
        status = "withheld"
    elif scale.require_all_subjects_pass and any(o.status == "fail" for o in lines):
        status = "fail"
    elif scale.overall_pass_percentage is not None and pct < scale.overall_pass_percentage:
        status = "fail"
    else:
        status = "pass"

    complete = status in ("pass", "fail")
    return Overall(
        total_obtained=total_obtained, total_full=total_full, percentage=pct, grade_point=gpa,
        letter=scale.letter_for_gpa(gpa, pct) if complete and counted else "",
        division=scale.division_for(pct) if status == "pass" else "",
        status=status,
    )


def combine_weighted(scale: Scale, parts: list[tuple[Decimal, dict, SubjectOutcome | None]], *,
                     subject_id, credit) -> SubjectOutcome:
    """One subject across several exams: ``parts`` are (weight, the exam as
    ``{"exam": id, "name": ...}``, that exam's outcome for the subject, or
    None if the exam had no paper in it).

    An exam without the subject, or where the student is exempt, doesn't
    count and the other weights are scaled up to fill the gap. Absent
    counts as 0 with its full weight. A withheld or unfinished part holds
    the whole subject back."""
    used = [(w, ref, o) for w, ref, o in parts if o is not None and o.status != "exempt"]
    out = SubjectOutcome(subject_id, None, Decimal(credit), full=HUNDRED)
    if not used:
        out.status = "exempt"
        return out
    for w, ref, o in used:
        out.detail.append({**ref, "weight": str(w), "percentage": str(o.percentage),
                           "status": o.status, "absent": o.absent})
    if any(o.status == "incomplete" for _, _, o in used):
        out.status = "incomplete"
        return out
    if any(o.status == "withheld" for _, _, o in used):
        out.status = "withheld"
        return out
    total_weight = sum((w for w, _, _ in used), ZERO)
    out.percentage = q2(sum((w * o.percentage for w, _, o in used), ZERO) / total_weight)
    out.obtained = out.percentage
    out.absent = all(o.absent for _, _, o in used)
    out.band = scale.band_for(out.percentage)
    out.status = "pass" if out.band.is_pass else "fail"
    return out


def rank(values: list[tuple[int, Decimal]]) -> dict[int, int]:
    """Competition ranking (1, 2, 2, 4): equal percentages share a place."""
    ordered = sorted(values, key=lambda v: -v[1])
    ranks, last, place = {}, None, 0
    for index, (key, value) in enumerate(ordered, start=1):
        if value != last:
            place, last = index, value
        ranks[key] = place
    return ranks
