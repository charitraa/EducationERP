from django.contrib import admin

from .models import Application, ApplicationEvent, ApplicationType, ApprovalStep, Certificate

for model in (ApplicationType, ApprovalStep, Application, ApplicationEvent, Certificate):
    admin.site.register(model)
