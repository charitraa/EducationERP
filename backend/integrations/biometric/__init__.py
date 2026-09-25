"""Biometric devices → the attendance service.

Adapters turn what a device sends into ``DevicePunch`` objects and hand
them to ``ingest``; nothing here decides attendance. Add a vendor by
writing an adapter, never by changing ``modules.attendance``.
"""
