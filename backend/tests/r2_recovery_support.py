"""Stage 7H.3: synthetic-only independent object backup and recovery harness.

Never selected by application runtime.  No Cloudflare client, credentials or SQL
writes. Every object is verified against the independent SQL archive ledger.
"""
from __future__ import annotations

import hashlib
import re

from sqlalchemy import select

from app.models import ArchiveJob
from app.storage.archive import _verify_object
from app.storage.r2 import InMemoryObjectStore

class RecoveryRejected(RuntimeError):
    pass

def _ledger(session):
    entries = session.scalars(select(ArchiveJob).order_by(ArchiveJob.r2_key)).all()
    if not entries:
        raise RecoveryRejected("ARCHIVE_LEDGER_EMPTY")
    if len(entries) > 100:
        raise RecoveryRejected("FIXTURE_SCOPE_LIMIT")
    if (any(item.status != "VERIFIED" or not item.verified_at
            or not item.checksum or not re.fullmatch(r"[0-9a-f]{64}", item.checksum)
            or not item.file_size_bytes or not item.row_count or not item.r2_key
            for item in entries)):
        raise RecoveryRejected("ARCHIVE_LEDGER_NOT_VERIFIED")
    if len({item.r2_key for item in entries}) != len(entries):
        raise RecoveryRejected("DUPLICATE_ARCHIVE_KEY")
    return entries

def _proof(store, item):
    try:
        _verify_object(store, item.r2_key, expected_size=item.file_size_bytes,
                       expected_rows=item.row_count, expected_checksum=item.checksum)
        data = store.get_object(item.r2_key)
        info = store.head_object(item.r2_key)
        if not info or not info.metadata: raise ValueError()
        return data, dict(info.metadata)
    except Exception:
        raise RecoveryRejected("OBJECT_READBACK_NOT_VERIFIED") from None

def backup(session, primary):
    """Produce an independent *in-memory* backup, fully cross-checked by SQL ledger."""
    rows = _ledger(session)
    secondary = InMemoryObjectStore()
    for item in rows:
        body, metadata = _proof(primary, item)
        secondary.put_object_if_absent(item.r2_key, body,
            content_type="application/vnd.apache.parquet", metadata=metadata)
        _proof(secondary, item)
    if len(secondary._objects) != len(rows):
        raise RecoveryRejected("OBJECT_INVENTORY_INCOMPLETE")
    return secondary

def restore(session, secondary, *, target):
    """Restore only to an empty in-memory store; reject any missing/extra object."""
    rows = _ledger(session)
    required = {item.r2_key for item in rows}
    if target.list_objects():
        raise RecoveryRejected("RESTORE_TARGET_NOT_EMPTY")
    present = {info.key for info in secondary.list_objects()}
    if present != required:
        raise RecoveryRejected("BACKUP_INVENTORY_MISMATCH")
    # Fail before *any* target write if backup proof does not match SQL ledger.
    verified = [(item.r2_key, *_proof(secondary,item)) for item in rows]
    for key, body, metadata in verified:
        target.put_object_if_absent(key,body,content_type="application/vnd.apache.parquet",metadata=metadata)
    if {info.key for info in target.list_objects()} != required:
        raise RecoveryRejected("RESTORE_INVENTORY_MISMATCH")
    for item in rows:
        _proof(target,item)
    return {"status":"PASS","objects":len(rows),"verified_sha256":hashlib.sha256(
        "|".join(f"{item.r2_key}:{item.checksum}" for item in rows).encode()).hexdigest()}
