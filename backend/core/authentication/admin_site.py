"""The Django admin with the same login protection as the API.

Without this, /admin/ would be a way around it: no attempt limit, and no
second factor for accounts that have one.
"""
from django import forms
from django.contrib import admin
from django.contrib.admin.forms import AdminAuthenticationForm
from django.http import HttpResponse

from core.audit.middleware import get_client_ip
from core.audit.services import log_login_failure

from . import lockout, two_factor


class AdminLoginForm(AdminAuthenticationForm):
    otp = forms.CharField(
        label="Two-factor code",
        required=False,
        help_text="Only if two-factor login is on for your account.",
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric"}),
    )

    def clean(self):
        cleaned = super().clean()  # checks the password and staff status
        user = self.get_user()
        if user is not None and two_factor.is_enabled(user):
            if not two_factor.verify(user, cleaned.get("otp") or ""):
                log_login_failure(user.email, "invalid_otp", self.request)
                raise forms.ValidationError(
                    "Enter a valid two-factor code from your authenticator app.",
                    code="invalid_otp",
                )
        return cleaned


class ERPAdminSite(admin.AdminSite):
    site_header = "Education ERP administration"
    site_title = "Education ERP admin"
    login_form = AdminLoginForm
    login_template = "erp_admin/login.html"

    def login(self, request, extra_context=None):
        if request.method != "POST":
            return super().login(request, extra_context)

        ip = get_client_ip(request) or "unknown"
        email = request.POST.get("username", "")
        if lockout.is_locked(lockout.IP, ip) or lockout.is_locked(lockout.ACCOUNT, email):
            log_login_failure(email, "locked", request)
            return HttpResponse(
                "Too many failed login attempts. Try again later.",
                status=429,
                content_type="text/plain; charset=utf-8",
            )

        response = super().login(request, extra_context)
        # Success is a redirect into the admin; anything else re-shows the form.
        if request.user.is_authenticated and response.status_code in (301, 302):
            lockout.reset(lockout.ACCOUNT, email)
        else:
            lockout.record_failure(lockout.ACCOUNT, email)
            lockout.record_failure(lockout.IP, ip)
        return response
