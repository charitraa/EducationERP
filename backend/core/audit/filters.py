import django_filters as filters

from .models import AuditLog


class AuditLogFilter(filters.FilterSet):
    """Answers the questions an auditor actually asks."""

    action = filters.MultipleChoiceFilter(choices=AuditLog.Action.choices)
    module = filters.CharFilter(lookup_expr="iexact")
    object_type = filters.CharFilter(lookup_expr="iexact")
    actor_email = filters.CharFilter(lookup_expr="icontains")
    created_after = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lte")

    class Meta:
        model = AuditLog
        fields = ["action", "module", "object_type", "object_id", "actor", "actor_email"]
