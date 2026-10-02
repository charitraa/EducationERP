"""Business events other modules may react to, without this module knowing
who listens (claude.md: dependencies point down, events point up).

``student_graduated(sender=Student, student, enrollment, on_date, by)`` is
sent inside the status change's transaction, after the enrollment it
completed is closed. A receiver that raises rolls the graduation back.
"""
from django.dispatch import Signal

student_graduated = Signal()
