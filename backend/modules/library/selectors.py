"""Read-only views over the catalog and a member's own circulation history."""
from django.utils import timezone

from .models import Issue, IssueStatus, Member


def member_for_user(user) -> Member | None:
    return Member.objects.filter(student__user=user).first() or Member.objects.filter(staff__user=user).first()


def overdue_issues(organization_id):
    return (Issue.objects.filter(organization_id=organization_id, status=IssueStatus.ISSUED,
                                 due_at__lt=timezone.now())
           .select_related("copy__book", "member"))
