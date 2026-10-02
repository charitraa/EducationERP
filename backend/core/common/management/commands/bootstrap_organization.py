"""Create an organization with a main campus and its first administrator.

The usual way to onboard a tenant:

    python manage.py bootstrap_organization \
        --name "Central College" --code central-college \
        --admin-email admin@central.edu --admin-password '...'
"""
import secrets

from django.core.management.base import BaseCommand, CommandError

from core.common.exceptions import ServiceError
from core.organizations.models import Organization
from core.organizations.services import create_organization


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

    def handle(self, *args, **options):
        # Checked here too: argparse `choices` only applies on the command
        # line, not when the command is called from code via call_command().
        if options["type"] not in Organization.Type.values:
            raise CommandError(
                f"Unknown type '{options['type']}'. "
                f"Choose one of: {', '.join(Organization.Type.values)}."
            )

        password = options["admin_password"] or secrets.token_urlsafe(16)
        try:
            organization, _, admin = create_organization(
                name=options["name"], code=options["code"], type=options["type"],
                campus_name=options["campus_name"], campus_code=options["campus_code"],
                admin_email=options["admin_email"], admin_password=password,
            )
        except ServiceError as exc:
            raise CommandError(exc.detail) from exc

        self.stdout.write(self.style.SUCCESS(f"Created organization: {organization.name}"))
        self.stdout.write(self.style.SUCCESS(f"Admin user: {admin.email}"))
        if not options["admin_password"]:
            self.stdout.write(self.style.WARNING(f"Generated password: {password}"))
