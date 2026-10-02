"""Storing and attaching files.

Every upload is checked before anything is written:

* **size**: at most the purpose's limit (``FILE_UPLOAD_MAX_BYTES`` by
  default), and not empty.
* **type**: decided from the file's first bytes, never from the name or the
  client's ``Content-Type``. Only PDF, PNG, JPEG and Word (.docx) exist;
  HTML, SVG and the like can't be stored, so a download can't run script.
  A .docx carrying macros is refused.
* **name**: the extension must agree with the bytes. The name is cleaned and
  kept only for display; the stored path is generated.
"""
import hashlib
import os
import re
import unicodedata
import zipfile

from django.conf import settings
from django.db import transaction

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, PermissionDeniedError, ServiceError

from .models import StoredFile

MODULE = "files"

# type -> (content type, extensions it may be named with)
TYPES = {
    "pdf": ("application/pdf", {"pdf"}),
    "png": ("image/png", {"png"}),
    "jpeg": ("image/jpeg", {"jpg", "jpeg"}),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", {"docx"}),
}

# purpose -> the types it accepts. Modules add theirs with register_purpose.
PURPOSES: dict[str, set[str]] = {}


def register_purpose(purpose: str, types) -> None:
    unknown = set(types) - set(TYPES)
    if unknown:
        raise ValueError(f"Unknown file types: {unknown}")
    PURPOSES[purpose] = set(types)


def max_bytes() -> int:
    return settings.FILE_UPLOAD_MAX_BYTES


def sniff(upload) -> str | None:
    """The file's real type from its bytes, or ``None``."""
    upload.seek(0)
    head = upload.read(8)
    upload.seek(0)
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(upload) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile:
            return None
        finally:
            upload.seek(0)
        if "word/document.xml" in names and "[Content_Types].xml" in names and "word/vbaProject.bin" not in names:
            return "docx"
    return None


def clean_name(raw: str) -> str:
    name = os.path.basename(str(raw).replace("\\", "/"))
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"[\x00-\x1f\x7f\"<>|:*?]", "", name).strip(" .")
    if len(name) > 150:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 150 - len(ext) - 1] + dot + ext) if dot else name[:150]
    return name or "file"


def check(upload, purpose: str) -> tuple[str, str]:
    """Validate an upload for ``purpose``; returns (type, cleaned name)."""
    if purpose not in PURPOSES:
        raise ServiceError(f"Unknown purpose. One of: {', '.join(sorted(PURPOSES))}.", code="unknown_purpose")
    if upload.size == 0:
        raise ServiceError("The file is empty.", code="empty_file")
    limit = max_bytes()
    if upload.size > limit:
        raise ServiceError(f"The file is larger than {limit // (1024 * 1024)} MB.", code="file_too_large")
    kind = sniff(upload)
    allowed = PURPOSES[purpose]
    if kind is None or kind not in allowed:
        names = ", ".join(sorted(ext for t in allowed for ext in TYPES[t][1]))
        raise ServiceError(f"This kind of file isn't accepted here. Accepted: {names}.", code="file_type")
    name = clean_name(getattr(upload, "name", "") or "file")
    extension = name.rpartition(".")[2].lower() if "." in name else ""
    if extension not in TYPES[kind][1]:
        raise ServiceError("The file name's extension doesn't match what the file is.", code="file_type")
    return kind, name


def store(upload, *, organization_id: int, purpose: str, by=None) -> StoredFile:
    kind, name = check(upload, purpose)
    digest = hashlib.sha256()
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)
    with transaction.atomic():
        stored = StoredFile(organization_id=organization_id, name=name, content_type=TYPES[kind][0],
                            extension=name.rpartition(".")[2].lower(), size=upload.size,
                            sha256=digest.hexdigest(), purpose=purpose,
                            uploaded_by=by if by is not None and by.is_authenticated else None)
        stored.file.save(name, upload, save=False)
        stored.save()
        log(AuditLog.Action.CREATE, instance=stored, module=MODULE, actor=stored.uploaded_by)
    return stored


def attach(stored: StoredFile, *, owner_type: str, owner_id: int, purpose: str, by=None) -> StoredFile:
    """Hand a file to the record that uses it. Only its uploader can attach
    it (``by=None`` for a file stored in the same public request), and only
    once, so nobody can pull another person's upload into their record."""
    with transaction.atomic():
        stored = StoredFile.objects.select_for_update().get(pk=stored.pk)
        if stored.purpose != purpose:
            raise ServiceError(f"This file was uploaded as {stored.purpose}, not {purpose}.", code="wrong_purpose")
        if stored.is_attached:
            raise ConflictError("This file is already attached to something else.", code="already_attached")
        if by is not None and stored.uploaded_by_id != by.pk:
            raise PermissionDeniedError("You can only attach a file you uploaded.", code="not_your_file")
        stored.owner_type, stored.owner_id = owner_type, owner_id
        stored.save(update_fields=["owner_type", "owner_id", "updated_at"])
    return stored


def discard(stored: StoredFile, *, by) -> None:
    """The uploader removes a file nothing uses yet. Attached files stay
    with their record."""
    if stored.is_attached:
        raise ConflictError("This file is attached to a record and stays with it.", code="attached")
    if stored.uploaded_by_id != by.pk and not by.is_superuser:
        raise PermissionDeniedError("Only the uploader can remove it.", code="not_your_file")
    with transaction.atomic():
        log(AuditLog.Action.DELETE, instance=stored, module=MODULE, actor=by)
        stored.file.delete(save=False)
        stored.hard_delete()
