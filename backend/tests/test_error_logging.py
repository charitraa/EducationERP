"""Server-error logging.

A 500 that is not written down is a 500 nobody can fix. These pin the three
things support actually needs: the traceback reaches the error log, the log
line names the request, and the client is told the same id.
"""
import contextlib
import logging

from django.test import SimpleTestCase, override_settings

from core.common.logging import RequestContextFilter
from core.common.middleware import REQUEST_ID_HEADER

BOOM_URLS = "tests.urls_boom"


class _CapturingHandler(logging.Handler):
    """Collects records with the project's filter applied at emit time.

    `assertLogs` installs its own handler with no filters, and the request
    context is gone by the time the response comes back — so the enriched
    fields have to be captured while the request is still in flight, exactly
    as the real file handler sees them.
    """

    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []
        self.addFilter(RequestContextFilter())

    def emit(self, record):
        self.records.append(record)


@contextlib.contextmanager
def capture(logger_name="django.request", level=logging.ERROR):
    logger = logging.getLogger(logger_name)
    handler = _CapturingHandler()
    logger.addHandler(handler)
    previous, logger.level = logger.level, level
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.level = previous


@override_settings(ROOT_URLCONF=BOOM_URLS, DEBUG=False, ALLOWED_HOSTS=["*"])
class ServerErrorLoggingTests(SimpleTestCase):
    def test_unhandled_exception_is_logged_with_traceback(self):
        with self.assertLogs("django.request", level="ERROR") as captured:
            with self.assertRaises(RuntimeError):
                self.client.get("/api/v1/boom/")

        record = captured.records[0]
        self.assertEqual(record.levelno, logging.ERROR)
        self.assertIn("/api/v1/boom/", record.getMessage())
        # exc_info is what carries the traceback to the handler.
        self.assertIsNotNone(record.exc_info)
        self.assertIsInstance(record.exc_info[1], RuntimeError)

    def test_log_record_carries_request_context(self):
        with capture() as handler:
            with self.assertRaises(RuntimeError):
                self.client.get("/api/v1/boom/?page=2")

        record = handler.records[0]
        self.assertEqual(record.method, "GET")
        self.assertEqual(record.path, "/api/v1/boom/?page=2")
        self.assertEqual(record.user_id, "anonymous")
        self.assertNotEqual(record.request_id, "-")

    def test_logged_id_matches_the_one_returned_to_the_client(self):
        # The whole point of the id: support is handed one by the user and has
        # to find the matching traceback. Let the client return the real 500
        # instead of re-raising, so both sides can be compared.
        self.client.raise_request_exception = False

        with capture() as handler:
            response = self.client.get("/api/v1/boom/")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(handler.records[0].request_id, response[REQUEST_ID_HEADER])

    def test_upstream_request_id_is_reused_rather_than_replaced(self):
        # A gateway or the SPA may already have started the trace.
        supplied = "trace-abc-123"
        self.client.raise_request_exception = False

        with capture() as handler:
            response = self.client.get("/api/v1/boom/", HTTP_X_REQUEST_ID=supplied)

        self.assertEqual(response[REQUEST_ID_HEADER], supplied)
        self.assertEqual(handler.records[0].request_id, supplied)

    def test_successful_response_returns_a_request_id(self):
        response = self.client.get("/health/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response[REQUEST_ID_HEADER])

    def test_supplied_request_id_is_sanitised_before_being_echoed(self):
        # The id is written to the log file and reflected to the client, so a
        # header carrying newlines or control characters must not survive.
        response = self.client.get(
            "/health/", HTTP_X_REQUEST_ID="abc\r\nInjected: yes"
        )

        returned = response[REQUEST_ID_HEADER]
        self.assertNotIn("\r", returned)
        self.assertNotIn("\n", returned)
        self.assertNotIn(":", returned)
        self.assertTrue(all(c.isalnum() or c in "-_" for c in returned), returned)

    def test_overlong_request_id_is_truncated(self):
        response = self.client.get("/health/", HTTP_X_REQUEST_ID="a" * 500)

        self.assertEqual(len(response[REQUEST_ID_HEADER]), 64)

    def test_request_id_does_not_leak_between_requests(self):
        first = self.client.get("/health/")[REQUEST_ID_HEADER]
        second = self.client.get("/health/")[REQUEST_ID_HEADER]

        self.assertNotEqual(first, second)


class RequestContextFilterTests(SimpleTestCase):
    def test_filter_fills_blanks_outside_a_request(self):
        # Management commands and background code log with no request in
        # context; the formatter must still have every field it references.
        record = logging.LogRecord(
            "core.test", logging.ERROR, __file__, 1, "offline failure", None, None
        )

        self.assertTrue(RequestContextFilter().filter(record))
        self.assertEqual(record.method, "-")
        self.assertEqual(record.path, "-")
        self.assertEqual(record.user_id, "-")
        self.assertEqual(record.request_id, "-")
