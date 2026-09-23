"""Create an organization with a main campus and its first administrator.

The usual way to onboard a tenant:

    python manage.py bootstrap_organization \
        --name "Central College" --code central-college \
        --admin-email admin@central.edu --admin-password '...'
"""
import secrets

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.accounts.services import create_user
from core.organizations.models import Campus, Organization
from core.permissions.models import Role


class Command(BaseCommand):
    help = "Create an organization, a main campus and an org-admin user."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--code", required=True)
        parser.add_argument(
            "--type", default=Organization.Type.COLLEGE, choices=Organization.Type.values
        )
        parser.add_argument("--campus-name", default="Main Campus")
        parser.add_argument("--campus-code", default="main")
        parser.add_argument("--admin-email", required=True)
        parser.add_argument(
            "--admin-password",
            default=None,
            help="Generated and printed if omitted.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        code = options["code"].lower()
        # Checked here too: argparse `choices` only applies on the command
        # line, not when the command is called from code via call_command().
        if options["type"] not in Organization.Type.values:
            raise CommandError(
                f"Unknown type '{options['type']}'. "
                f"Choose one of: {', '.join(Organization.Type.values)}."
            )
        if Organization.all_objects.filter(code=code).exists():
            raise CommandError(f"Organization '{code}' already exists.")

        admin_role = Role.objects.filter(code="org-admin", organization=None).first()
        if admin_role is None:
            raise CommandError(
                "System roles are missing. Run 'manage.py sync_permissions' first."
            )

        organization = Organization.objects.create(
            name=options["name"], code=code, type=options["type"]
        )
        Campus.objects.create(
            organization=organization,
            name=options["campus_name"],
            code=options["campus_code"].lower(),
            is_main=True,
        )

        password = options["admin_password"] or secrets.token_urlsafe(16)
        admin = create_user(
            email=options["admin_email"],
            password=password,
            organization=organization,
            user_type="administrator",
            first_name="Organization",
            last_name="Administrator",
            role_codes=["org-admin"],
        )

        self.stdout.write(self.style.SUCCESS(f"Created organization: {organization.name}"))
        self.stdout.write(self.style.SUCCESS(f"Admin user: {admin.email}"))
        if not options["admin_password"]:
            self.stdout.write(self.style.WARNING(f"Generated password: {password}"))
