"""Delete signups whose verification link expired long ago and was never
opened. Safe to run from cron daily."""
from django.core.management.base import BaseCommand

from core.signup.services import purge_unverified


class Command(BaseCommand):
    help = "Delete unverified signup requests whose link expired more than --days ago."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)

    def handle(self, *args, **options):
        deleted = purge_unverified(older_than_days=options["days"])
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} unverified signup request(s)."))
