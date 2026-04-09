from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from services.csv_normalizer import CSVNormalizationError, normalize_csv_for_veriphone
from services.csv_inspector import CSVInspectionError, inspect_csv
from services.result_filter import ResultProcessingError, filter_verified_results
from services.veriphone_client import VeriphoneAPIError, VeriphoneClient

APP_ROOT = Path(__file__).resolve().parent
TMP_ROOT = APP_ROOT / ".tmp"
UPLOAD_DIR = TMP_ROOT / "uploads"
JOB_DIR = TMP_ROOT / "jobs"
DOWNLOAD_DIR = TMP_ROOT / "downloads"
EXPORT_DIR = TMP_ROOT / "exports"
DEFAULT_COUNTRY = "US"
MAX_FILE_SIZE_MB = 100


class ConfigurationError(RuntimeError):
    """Raised when required local configuration is missing."""


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE_MB * 1024 * 1024

    load_dotenv(APP_ROOT / ".env")
    for directory in (TMP_ROOT, UPLOAD_DIR, JOB_DIR, DOWNLOAD_DIR, EXPORT_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    @app.get("/")
    def index() -> str:
        return render_template(
            "index.html",
            default_country_label="USA",
            max_file_size_mb=MAX_FILE_SIZE_MB,
        )

    @app.get("/favicon.ico")
    def favicon() -> tuple[str, int]:
        return ("", 204)

    @app.post("/api/uploads/inspect")
    def inspect_upload() -> Any:
        try:
            upload = request.files.get("file")
            if upload is None or not upload.filename:
                return _api_error("Choose a CSV file before uploading.", 400)

            filename = secure_filename(upload.filename)
            if not filename.lower().endswith(".csv"):
                return _api_error("Only CSV uploads are supported.", 400)

            upload_id = uuid.uuid4().hex
            raw_path = UPLOAD_DIR / f"{upload_id}_raw_{filename}"
            normalized_path = UPLOAD_DIR / f"{upload_id}_normalized.csv"
            upload.save(raw_path)
            normalize_csv_for_veriphone(raw_path, normalized_path)

            inspection = inspect_csv(normalized_path)
            manifest = {
                "upload_id": upload_id,
                "filename": filename,
                "raw_path": str(raw_path),
                "stored_path": str(normalized_path),
                "created_at": time.time(),
                "inspection": inspection,
            }
            _write_json(_upload_manifest_path(upload_id), manifest)

            return jsonify(
                {
                    "status": "success",
                    "upload_id": upload_id,
                    "filename": filename,
                    **inspection,
                }
            )
        except CSVInspectionError as error:
            return _api_error(str(error), 400)
        except CSVNormalizationError as error:
            return _api_error(str(error), 400)
        except Exception as error:  # pragma: no cover - defensive fallback
            return _api_error(f"Could not inspect the CSV upload: {error}", 500)

    @app.post("/api/jobs")
    def start_job() -> Any:
        payload = request.get_json(silent=True) or {}

        try:
            upload_id = str(payload.get("upload_id", "")).strip()
            if not upload_id:
                return _api_error("Missing upload_id.", 400)

            upload_manifest = _read_json(_upload_manifest_path(upload_id))
            inspection = upload_manifest["inspection"]
            column_index = _require_int(payload.get("phone_column_index"), "phone_column_index")
            first_data_row = _require_int(payload.get("first_data_row"), "first_data_row")

            columns = inspection["columns"]
            if column_index < 0 or column_index >= len(columns):
                return _api_error("phone_column_index is outside the CSV column range.", 400)

            client = _build_client()
            remote_upload = client.upload_file(
                csv_path=Path(upload_manifest["stored_path"]),
                column=column_index,
                firstrow=first_data_row,
            )

            remote_id = str(remote_upload.get("id", "")).strip()
            if not remote_id:
                raise VeriphoneAPIError("Veriphone did not return an upload id.", status_code=502)

            client.start_verification(remote_id, default_country=DEFAULT_COUNTRY)

            job_id = uuid.uuid4().hex
            selected_column = columns[column_index]
            job_record = {
                "job_id": job_id,
                "status": "verifying",
                "filename": upload_manifest["filename"],
                "upload_id": upload_id,
                "original_file_path": upload_manifest["stored_path"],
                "inspection": inspection,
                "selected_column": selected_column["label"],
                "selected_column_index": selected_column["index"],
                "first_data_row": first_data_row,
                "default_country": DEFAULT_COUNTRY,
                "veriphone_upload_id": remote_id,
                "created_at": time.time(),
                "remote_upload_response": remote_upload,
                "progress": {
                    "lastrow": remote_upload.get("lastrow"),
                    "position": 0,
                    "stage": "queued",
                    "speed": 0,
                    "billable": 0,
                    "syntaxerr": 0,
                    "valid": 0,
                    "invalid": 0,
                },
            }
            _write_json(_job_manifest_path(job_id), job_record)

            refreshed_job = _refresh_job(job_record, client)
            _write_json(_job_manifest_path(job_id), refreshed_job)
            return jsonify(_serialize_job(refreshed_job))
        except FileNotFoundError:
            return _api_error("The uploaded CSV could not be found. Re-upload the file and try again.", 404)
        except ConfigurationError as error:
            return _api_error(str(error), 500)
        except VeriphoneAPIError as error:
            return _api_error(error.message, error.status_code)
        except ValueError as error:
            return _api_error(str(error), 400)
        except Exception as error:  # pragma: no cover - defensive fallback
            return _api_error(f"Could not start the Veriphone job: {error}", 500)

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id: str) -> Any:
        try:
            job_record = _read_json(_job_manifest_path(job_id))
            if job_record["status"] not in {"completed", "error"}:
                client = _build_client()
                job_record = _refresh_job(job_record, client)
                _write_json(_job_manifest_path(job_id), job_record)

            return jsonify(_serialize_job(job_record))
        except FileNotFoundError:
            return _api_error("Job not found.", 404)
        except ConfigurationError as error:
            return _api_error(str(error), 500)
        except VeriphoneAPIError as error:
            job_record = _safe_read_json(_job_manifest_path(job_id))
            if job_record:
                job_record["status"] = "error"
                job_record["error"] = error.message
                _write_json(_job_manifest_path(job_id), job_record)
            return _api_error(error.message, error.status_code)
        except ResultProcessingError as error:
            job_record = _safe_read_json(_job_manifest_path(job_id))
            if job_record:
                job_record["status"] = "error"
                job_record["error"] = str(error)
                _write_json(_job_manifest_path(job_id), job_record)
            return _api_error(str(error), 500)
        except Exception as error:  # pragma: no cover - defensive fallback
            return _api_error(f"Could not fetch job status: {error}", 500)

    @app.get("/api/jobs/<job_id>/download")
    def download_filtered_file(job_id: str) -> Any:
        try:
            job_record = _read_json(_job_manifest_path(job_id))
            export_path = job_record.get("export_path")
            if not export_path:
                return _api_error("The filtered CSV is not ready yet.", 409)

            file_path = Path(export_path)
            if not file_path.exists():
                return _api_error("The filtered CSV could not be found.", 404)

            return send_file(
                file_path,
                as_attachment=True,
                download_name=file_path.name,
                mimetype="text/csv",
            )
        except FileNotFoundError:
            return _api_error("Job not found.", 404)

    return app


def _refresh_job(job_record: Dict[str, Any], client: VeriphoneClient) -> Dict[str, Any]:
    remote_id = job_record["veriphone_upload_id"]
    status_payload = client.get_status(remote_id)
    details_payload = client.get_file_details(remote_id)

    remote_status = (
        str(status_payload.get("status", "")).strip()
        or str(details_payload.get("status", "")).strip()
        or str(job_record.get("status", "")).strip()
    )
    if not remote_status:
        raise VeriphoneAPIError(
            "Veriphone returned an empty status for this upload.",
            status_code=404,
        )

    remote_stage = str(details_payload.get("stage", "")).strip().lower()
    completion_markers = {"completed", "complete", "done"}

    job_record["remote_status"] = remote_status
    job_record["progress"] = {
        "lastrow": details_payload.get("lastrow") or job_record.get("progress", {}).get("lastrow"),
        "position": details_payload.get("position", 0),
        "stage": details_payload.get("stage", remote_status),
        "speed": details_payload.get("speed", 0),
        "billable": details_payload.get("billable", 0),
        "syntaxerr": details_payload.get("syntaxerr", 0),
        "valid": details_payload.get("valid", 0),
        "invalid": details_payload.get("invalid", 0),
    }

    if remote_status.lower() in completion_markers or remote_stage in completion_markers:
        if not job_record.get("export_path"):
            download_path = DOWNLOAD_DIR / f"{job_record['job_id']}_veriphone_results.csv"
            export_name = f"{Path(job_record['filename']).stem}_mobile_only.csv"
            export_path = EXPORT_DIR / f"{job_record['job_id']}_{export_name}"

            client.download_results(remote_id, download_path)
            summary = filter_verified_results(
                original_csv_path=Path(job_record["original_file_path"]),
                verified_csv_path=download_path,
                output_csv_path=export_path,
                has_header=bool(job_record["inspection"]["has_header"]),
                original_column_labels=[
                    str(column["label"]) for column in job_record["inspection"]["columns"]
                ],
            )
            job_record["downloaded_result_path"] = str(download_path)
            job_record["export_path"] = str(export_path)
            job_record["summary"] = summary

        job_record["status"] = "completed"
        return job_record

    if remote_status in {"deleted"}:
        job_record["status"] = "error"
        job_record["error"] = "The remote Veriphone upload is no longer available."
        return job_record

    job_record["status"] = "verifying"
    return job_record


def _serialize_job(job_record: Dict[str, Any]) -> Dict[str, Any]:
    export_path = job_record.get("export_path")
    return {
        "status": "success",
        "job_id": job_record["job_id"],
        "filename": job_record["filename"],
        "job_status": job_record["status"],
        "remote_status": job_record.get("remote_status", job_record["status"]),
        "default_country": job_record["default_country"],
        "selected_column": job_record["selected_column"],
        "first_data_row": job_record["first_data_row"],
        "progress": job_record.get("progress", {}),
        "summary": job_record.get("summary"),
        "download_ready": bool(export_path),
        "download_url": f"/api/jobs/{job_record['job_id']}/download" if export_path else None,
        "error": job_record.get("error"),
    }


def _build_client() -> VeriphoneClient:
    api_key = os.getenv("VERIPHONE_API_KEY", "").strip()
    if not api_key:
        raise ConfigurationError(
            "VERIPHONE_API_KEY is missing. Add it to local_workflows/03_veriphone_mobile_filter/.env."
        )
    return VeriphoneClient(api_key=api_key)


def _upload_manifest_path(upload_id: str) -> Path:
    return UPLOAD_DIR / f"{upload_id}.json"


def _job_manifest_path(job_id: str) -> Path:
    return JOB_DIR / f"{job_id}.json"


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_read_json(path: Path) -> Dict[str, Any] | None:
    if not path.exists():
        return None
    return _read_json(path)


def _require_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be an integer.") from error


def _api_error(message: str, status_code: int) -> Any:
    response = jsonify({"status": "error", "message": message})
    response.status_code = status_code
    return response


app = create_app()


if __name__ == "__main__":
    debug_enabled = os.getenv("VERIPHONE_DEBUG", "").strip() == "1"
    app.run(host="127.0.0.1", port=5000, debug=debug_enabled)
