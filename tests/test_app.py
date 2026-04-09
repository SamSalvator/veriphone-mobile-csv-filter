from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import _refresh_job


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


if __name__ == "__main__":
    unittest.main()
