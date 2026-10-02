from django.contrib import admin

from .models import (
    Achievement,
    AlumniEvent,
    AlumniProfile,
    Campaign,
    Donation,
    DonationRefund,
    Employment,
    HigherStudy,
    Mentorship,
    Rsvp,
)

for model in (AlumniProfile, Employment, HigherStudy, Achievement, AlumniEvent, Rsvp, Mentorship, Campaign,
              Donation, DonationRefund):
    admin.site.register(model)
