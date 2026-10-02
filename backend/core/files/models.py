"""Uploaded files, kept private.

A file is uploaded on its own (``POST /files/``) and then **attached** by
the module that uses it (a résumé to a job application). Until then only
the uploader can see it. Once attached, the owning module decides who can
read it, through ``access.register``. Bytes live in the ``private``
storage, never under ``MEDIA_ROOT``, and are only ever served through the
download endpoint after that check.
"""
import uuid

from django.core.files.storage import storages
from django.db import models

from core.common.models import OrganizationOwnedModel


def private_storage():
    return storages["private"]


def _path(instance, filename):
    # The stored name is ours: the client's name is kept as data only, so it
    # can never steer where the bytes land.
    return f"{instance.organization_id}/{uuid.uuid4().hex}.{instance.extension}"


class StoredFile(OrganizationOwnedModel):
    file = models.FileField(upload_to=_path, storage=private_storage, max_length=200)
    name = models.CharField(max_length=150, help_text="The name it was uploaded with, cleaned.")
    content_type = models.CharField(max_length=100, help_text="From the file's own bytes, not the client.")
    extension = models.CharField(max_length=10)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    purpose = models.CharField(max_length=30, help_text="What it was uploaded for, e.g. resume.")
    uploaded_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", help_text="Empty for a public form (no account).")
    owner_type = models.CharField(max_length=50, blank=True, help_text="What it is attached to, once it is.")
    owner_id = models.PositiveBigIntegerField(null=True, blank=True)

    class Meta:
        db_table = "files_stored_file"
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["owner_type", "owner_id"])]

    def __str__(self):
        return self.name

    @property
    def is_attached(self) -> bool:
        return bool(self.owner_type)
