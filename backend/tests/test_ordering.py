"""Every paginated list has a stable order.

Without an ORDER BY, page 2 can repeat or skip rows of page 1. Models
declare ``Meta.ordering``, but Django drops it from any query with a
GROUP BY, which is what an ``annotate(Count(...))`` makes. Such a view must
order explicitly.
"""
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from tests.factories import create_superuser
from tests.test_tenant_sweep import api_routes


class ListOrderingTests(TestCase):
    def test_every_list_queryset_is_ordered(self):
        user = create_superuser()
        routes, _ = api_routes()
        checked = 0
        for route in routes:
            if route.is_detail or route.actions.get("get") != "list":
                continue
            request = APIRequestFactory().get("/")
            force_authenticate(request, user=user)
            view = route.view(action_map={"get": "list"}, format_kwarg=None, args=(), kwargs={})
            view.request = view.initialize_request(request)
            view.action = "list"
            with self.subTest(view=route.view.__name__):
                self.assertTrue(view.get_queryset().ordered, f"{route.view.__name__} lists in no fixed order")
            checked += 1
        self.assertGreater(checked, 100)
