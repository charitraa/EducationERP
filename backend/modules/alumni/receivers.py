from django.dispatch import receiver

from modules.students.signals import student_graduated

from . import services


@receiver(student_graduated, dispatch_uid="alumni.profile_on_graduation")
def make_alumni_profile(sender, student, enrollment, on_date, by=None, **kwargs):
    services.profile_for_graduate(student, enrollment=enrollment, on_date=on_date, by=by)
