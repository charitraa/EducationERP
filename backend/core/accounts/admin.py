from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from .models import User


class UserCreateForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email",)


class UserEditForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = UserCreateForm
    form = UserEditForm
    model = User

    list_display = ["email", "full_name", "organization", "user_type", "is_active"]
    list_filter = ["user_type", "is_active", "is_superuser", "organization"]
    search_fields = ["email", "first_name", "last_name", "phone"]
    ordering = ["email"]
    readonly_fields = ["last_login", "created_at", "updated_at", "last_login_ip"]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal", {"fields": ("first_name", "middle_name", "last_name", "phone")}),
        ("Organization", {"fields": ("organization", "user_type")}),
        ("Status", {"fields": ("is_active", "is_staff", "is_superuser")}),
        ("Audit", {"fields": ("last_login", "last_login_ip", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "organization", "user_type", "password1", "password2"),
            },
        ),
    )
