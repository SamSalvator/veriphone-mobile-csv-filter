from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from services.storage import StorageBackend

RETENTION_PREFIXES = (
    "uploads/",
    "exports/",
    "jobs/",
    "veriphone/uploads/",
)


def purge_expired_objects(
    storage: StorageBackend,
    *,
    now: datetime | None = None,
    retention_days: int,
    prefixes: Iterable[str] = RETENTION_PREFIXES,
) -> dict[str, Any]:
    current_time = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    cutoff = current_time - timedelta(days=retention_days)

    expired_paths: list[str] = []
    scanned_by_prefix: dict[str, int] = {}

    for prefix in prefixes:
        scanned_count = 0
        for item in storage.list_objects(prefix=prefix):
            scanned_count += 1
            if item.uploaded_at <= cutoff:
                expired_paths.append(item.pathname)
        scanned_by_prefix[prefix] = scanned_count

    unique_expired_paths = list(dict.fromkeys(expired_paths))
    for batch_start in range(0, len(unique_expired_paths), 100):
        storage.delete_objects(unique_expired_paths[batch_start : batch_start + 100])

    return {
        "retention_days": retention_days,
        "cutoff": cutoff.isoformat(),
        "scanned_count": sum(scanned_by_prefix.values()),
        "scanned_by_prefix": scanned_by_prefix,
        "deleted_count": len(unique_expired_paths),
        "deleted_paths": unique_expired_paths,
    }
