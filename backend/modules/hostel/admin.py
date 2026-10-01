from django.contrib import admin

from .models import Allocation, Bed, Building, Complaint, Floor, HostelRoom, RoomType

for model in (Building, Floor, RoomType, HostelRoom, Bed, Allocation, Complaint):
    admin.site.register(model)
