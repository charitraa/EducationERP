from django.contrib import admin

from .models import Assignment, Driver, FuelLog, Maintenance, Route, Stop, Trip, TripRecord, Vehicle, VehicleDocument

for model in (Vehicle, VehicleDocument, Driver, Route, Stop, Assignment, Trip, TripRecord, Maintenance, FuelLog):
    admin.site.register(model)
