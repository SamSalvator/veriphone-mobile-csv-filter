from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import app
from services.retention import purge_expired_objects
from services.storage import LocalStorageBackend


class RetentionCleanupTests(unittest.TestCase):
    def test_purge_expired_objects_deletes_only_old_tracked_prefixes(self) -> None:
        now = datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorageBackend(Path(tmpdir))

            old_paths = [
                "uploads/upload-old/manifest.json",
                "uploads/upload-old/normalized.csv",
                "exports/job-old/sample_mobile_only.csv",
                "jobs/job-old.json",
                "veriphone/uploads/raw-old.csv",
            ]
            fresh_paths = [
                "uploads/upload-fresh/manifest.json",
                "jobs/job-fresh.json",
            ]
            ignored_old_path = "notes/keep.txt"

            for storage_path in old_paths + fresh_paths + [ignored_old_path]:
                file_path = storage.resolve_path(storage_path)
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text("payload", encoding="utf-8")

            self._set_mtime(storage.resolve_path(ignored_old_path), now - timedelta(days=9))
            for storage_path in old_paths:
                self._set_mtime(storage.resolve_path(storage_path), now - timedelta(days=8))
            for storage_path in fresh_paths:
                self._set_mtime(storage.resolve_path(storage_path), now - timedelta(days=2))

            summary = purge_expired_objects(storage, now=now, retention_days=7)

            self.assertEqual(summary["deleted_count"], len(old_paths))
            for storage_path in old_paths:
                self.assertFalse(storage.resolve_path(storage_path).exists())
            for storage_path in fresh_paths + [ignored_old_path]:
                self.assertTrue(storage.resolve_path(storage_path).exists())

    def test_cron_cleanup_requires_valid_bearer_secret(self) -> None:
        with patch.dict(os.environ, {"CRON_SECRET": "top-secret"}, clear=False):
            response = app.test_client().get("/api/cron/blob-retention")

        self.assertEqual(response.status_code, 401)

    def test_cron_cleanup_deletes_old_files_with_valid_secret(self) -> None:
        now = datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorageBackend(Path(tmpdir))
            old_path = storage.resolve_path("exports/job-old/sample_mobile_only.csv")
            old_path.parent.mkdir(parents=True, exist_ok=True)
            old_path.write_text("name,phone\nAlice,+14155550123\n", encoding="utf-8")
            self._set_mtime(old_path, now - timedelta(days=8))

            with patch.dict(os.environ, {"CRON_SECRET": "top-secret"}, clear=False), patch(
                "app._storage_backend",
                return_value=storage,
            ), patch("app.datetime") as datetime_mock:
                datetime_mock.now.return_value = now
                datetime_mock.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
                response = app.test_client().get(
                    "/api/cron/blob-retention",
                    headers={"Authorization": "Bearer top-secret"},
                )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["deleted_count"], 1)
        self.assertFalse(old_path.exists())

    @staticmethod
    def _set_mtime(path: Path, value: datetime) -> None:
        timestamp = value.timestamp()
        os.utime(path, (timestamp, timestamp))


if __name__ == "__main__":
    unittest.main()
