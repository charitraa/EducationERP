"""Read-side queries for academics that other modules use (timetable, and
later attendance and exams). Everything answers "on a date" (default
today), so history stays true after students move or drop subjects."""
from django.db.models import Q
from django.utils import timezone

from modules.students.models import Enrollment, Student

from .models import CurriculumSubject, StudentElective


def is_elective(section, subject_id) -> bool:
    return CurriculumSubject.objects.filter(
        program_id=section.program_id, level=section.level, subject_id=subject_id, is_elective=True
    ).exists()


def students_taking(section, subject_id, on=None):
    """Students in ``section`` on ``on`` who take the subject: everyone for a
    compulsory subject, only those who chose it for an elective."""
    enrollments = Enrollment.objects.on(on).filter(section=section)
    if is_elective(section, subject_id):
        enrollments = enrollments.filter(
            pk__in=StudentElective.objects.on(on).filter(subject_id=subject_id).values("enrollment_id")
        )
    return Student.objects.select_related("campus").filter(pk__in=enrollments.values("student_id"))


def electives_share_students(section, subject_a, subject_b, on=None) -> bool:
    """Does any student in ``section`` take both electives? Counts choices in
    effect on ``on`` or later, since a timetable is for the weeks ahead."""
    if subject_a == subject_b:
        return True
    day = on or timezone.localdate()
    live = StudentElective.objects.filter(enrollment__section=section).filter(
        Q(ended_on__isnull=True) | Q(ended_on__gt=day),
    ).filter(Q(enrollment__ended_on__isnull=True) | Q(enrollment__ended_on__gt=day))
    enrollments_a = live.filter(subject_id=subject_a).values("enrollment_id")
    return live.filter(enrollment_id__in=enrollments_a, subject_id=subject_b).exists()


def chosen_elective_ids(enrollment, on=None) -> set[int]:
    return set(StudentElective.objects.on(on).filter(enrollment=enrollment).values_list("subject_id", flat=True))
