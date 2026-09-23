"""Read-side queries for academics that other modules use (timetable, and
later attendance and exams)."""
from modules.students.models import Enrollment, Student

from .models import CurriculumSubject, StudentElective


def is_elective(section, subject_id) -> bool:
    return CurriculumSubject.objects.filter(
        program_id=section.program_id, level=section.level, subject_id=subject_id, is_elective=True
    ).exists()


def students_taking(section, subject_id):
    """Students currently in ``section`` who take the subject: everyone for a
    compulsory subject, only those who chose it for an elective."""
    # One filter() call, so the elective is matched on the same (current)
    # enrollment, not on one from an earlier class.
    lookup = {"enrollments__section": section, "enrollments__status": Enrollment.Status.ACTIVE}
    if is_elective(section, subject_id):
        lookup["enrollments__electives__subject_id"] = subject_id
    return Student.objects.select_related("campus").filter(**lookup).distinct()


def electives_share_students(section, subject_a, subject_b) -> bool:
    """Does any student currently in ``section`` take both electives?"""
    if subject_a == subject_b:
        return True
    enrollments_a = StudentElective.objects.filter(
        enrollment__section=section, enrollment__status=Enrollment.Status.ACTIVE, subject_id=subject_a
    ).values("enrollment_id")
    return StudentElective.objects.filter(enrollment_id__in=enrollments_a, subject_id=subject_b).exists()


def chosen_elective_ids(enrollment) -> set[int]:
    return set(enrollment.electives.values_list("subject_id", flat=True))
