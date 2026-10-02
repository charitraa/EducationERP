from django.contrib import admin

from .models import Candidacy, Interview, JobOffer, JobPosting, Vacancy

for model in (Vacancy, Candidacy, Interview, JobOffer, JobPosting):
    admin.site.register(model)
