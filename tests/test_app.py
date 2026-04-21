from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app, _refresh_job
from services.storage import LocalStorageBackend


class FakeCompleteClient:
    def get_status(self, upload_id):
        del upload_id
        return {"status": "complete"}

    def get_file_details(self, upload_id):
        del upload_id
        return {
            "status": "complete",
            "position": 2,
            "stage": "complete",
            "speed": 0,
            "billable": 1,
            "syntaxerr": 1,
            "valid": 0,
            "invalid": 1,
            "lastrow": 3,
        }


class FakeStageCompleteClient:
    def get_status(self, upload_id):
        del upload_id
        return {"status": "verifying"}

    def get_file_details(self, upload_id):
        del upload_id
        return {
            "status": "verifying",
            "position": 2,
            "stage": "complete",
            "speed": 0,
            "billable": 1,
            "syntaxerr": 1,
            "valid": 0,
            "invalid": 1,
            "lastrow": 3,
        }


class AppRefreshTests(unittest.TestCase):
    def test_complete_status_is_treated_as_finished(self) -> None:
        export_path = Path(tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name)
        self.addCleanup(lambda: export_path.unlink(missing_ok=True))

        job_record = {
            "job_id": "job-123",
            "status": "verifying",
            "filename": "sample.csv",
            "upload_id": "upload-123",
            "original_file_path": "unused.csv",
            "inspection": {"has_header": True, "columns": []},
            "selected_column": "phone",
            "first_data_row": 1,
            "default_country": "US",
            "veriphone_upload_id": "remote-123",
            "progress": {"lastrow": 3},
            "export_path": str(export_path),
        }

        refreshed = _refresh_job(job_record, FakeCompleteClient())

        self.assertEqual(refreshed["status"], "completed")
        self.assertEqual(refreshed["remote_status"], "complete")

    def test_complete_stage_is_treated_as_finished(self) -> None:
        export_path = Path(tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name)
        self.addCleanup(lambda: export_path.unlink(missing_ok=True))

        job_record = {
            "job_id": "job-456",
            "status": "verifying",
            "filename": "sample.csv",
            "upload_id": "upload-456",
            "original_file_path": "unused.csv",
            "inspection": {"has_header": True, "columns": []},
            "selected_column": "phone",
            "first_data_row": 1,
            "default_country": "US",
            "veriphone_upload_id": "remote-456",
            "progress": {"lastrow": 3},
            "export_path": str(export_path),
        }

        refreshed = _refresh_job(job_record, FakeStageCompleteClient())

        self.assertEqual(refreshed["status"], "completed")
        self.assertEqual(refreshed["progress"]["stage"], "complete")

    def test_get_job_refreshes_completed_manifest_missing_export_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorageBackend(Path(tmpdir))
            job_id = "job-status"
            stale_record = self._build_stale_completed_record(job_id)
            storage.write_json(f"jobs/{job_id}.json", stale_record)

            refreshed_record = {
                **stale_record,
                "export_path": f"exports/{job_id}/sample_mobile_only.csv",
                "export_filename": "sample_mobile_only.csv",
                "summary": {"mobile_rows": 1, "excluded_rows": 0, "total_rows": 1},
            }

            with patch("app._storage_backend", return_value=storage), patch(
                "app._refresh_and_persist_job",
                return_value=refreshed_record,
            ) as refresh_mock:
                response = app.test_client().get(f"/api/jobs/{job_id}")

            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertTrue(payload["download_ready"])
            self.assertEqual(payload["job_status"], "completed")
            refresh_mock.assert_called_once()

    def test_download_route_refreshes_completed_manifest_missing_export_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorageBackend(Path(tmpdir))
            job_id = "job-download"
            stale_record = self._build_stale_completed_record(job_id)
            storage.write_json(f"jobs/{job_id}.json", stale_record)

            export_key = f"exports/{job_id}/sample_mobile_only.csv"
            export_path = storage.resolve_path(export_key)
            export_path.parent.mkdir(parents=True, exist_ok=True)
            export_path.write_text("name,phone\nAlice,+14155550123\n", encoding="utf-8")

            refreshed_record = {
                **stale_record,
                "export_path": export_key,
                "export_filename": "sample_mobile_only.csv",
                "summary": {"mobile_rows": 1, "excluded_rows": 0, "total_rows": 1},
            }

            with patch("app._storage_backend", return_value=storage), patch(
                "app._refresh_and_persist_job",
                return_value=refreshed_record,
            ) as refresh_mock:
                response = app.test_client().get(f"/api/jobs/{job_id}/download")

            self.assertEqual(response.status_code, 200)
            self.assertIn("Alice,+14155550123", response.get_data(as_text=True))
            response.close()
            refresh_mock.assert_called_once()

    @staticmethod
    def _build_stale_completed_record(job_id: str) -> dict:
        return {
            "job_id": job_id,
            "status": "completed",
            "remote_status": "complete",
            "filename": "sample.csv",
            "upload_id": "upload-123",
            "original_file_path": "uploads/upload-123/normalized.csv",
            "inspection": {
                "has_header": True,
                "columns": [
                    {"label": "name", "index": 0},
                    {"label": "phone", "index": 1},
                ],
            },
            "selected_column": "phone",
            "first_data_row": 1,
            "default_country": "US",
            "veriphone_upload_id": "remote-123",
            "progress": {"lastrow": 2, "position": 2, "stage": "complete"},
        }


if __name__ == "__main__":
    unittest.main()
