from django.contrib import admin

from .models import (
    PayComponent,
    PayrollAdjustment,
    PayrollRun,
    PayrollSettings,
    Payslip,
    PayslipLine,
    SalaryStructure,
    SalaryStructureLine,
    StaffSalary,
    StaffSalaryLine,
    TaxScheme,
    TaxSlab,
)

for model in (PayrollSettings, PayComponent, SalaryStructure, SalaryStructureLine, StaffSalary, StaffSalaryLine,
              TaxScheme, TaxSlab, PayrollRun, Payslip, PayslipLine, PayrollAdjustment):
    admin.site.register(model)
