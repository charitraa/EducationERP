"""Sync the code-declared permission catalogue and system roles into the DB.

Idempotent — safe to run on every deploy. This is the only place Permission
rows are created, which keeps the database in step with what the code enforces.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from core.permissions.models import Permission, Role
from core.permissions.registry import all_permissions, all_roles


class Command(BaseCommand):
    help = "Create or update permissions and system roles from the registry."

    def add_arguments(self, parser):
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Delete permissions that are no longer declared in code.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        verbose = options["verbosity"] > 0
        created, updated = 0, 0

        for spec in all_permissions():
            obj, was_created = Permission.objects.update_or_create(
                code=spec.code,
                defaults={
                    "module": spec.module,
                    "action": spec.action,
                    "name": spec.name,
                    "description": spec.description,
                },
            )
            created += was_created
            updated += not was_created

        if verbose:
            self.stdout.write(
                self.style.SUCCESS(f"Permissions: {created} created, {updated} updated.")
            )

        if options["prune"]:
            declared = {spec.code for spec in all_permissions()}
            stale = Permission.objects.exclude(code__in=declared)
            count = stale.count()
            stale.delete()
            if verbose:
                self.stdout.write(
                    self.style.WARNING(f"Pruned {count} stale permissions.")
                )

        roles_created, roles_updated = 0, 0
        for spec in all_roles():
            role, was_created = Role.objects.update_or_create(
                code=spec.code,
                organization=None,
                defaults={
                    "name": spec.name,
                    "description": spec.description,
                    "is_system": True,
                },
            )
            if spec.grants_all:
                role.permissions.set(Permission.objects.all())
            else:
                role.permissions.set(Permission.objects.filter(code__in=spec.permissions))

            roles_created += was_created
            roles_updated += not was_created

        if verbose:
            self.stdout.write(
                self.style.SUCCESS(
                    f"System roles: {roles_created} created, {roles_updated} updated."
                )
            )
