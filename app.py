from __future__ import annotations

import os
import tempfile
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
from flask import Response, Flask, jsonify, render_template, request, send_file, stream_with_context
from werkzeug.utils import secure_filename

from services.csv_normalizer import CSVNormalizationError, normalize_csv_for_veriphone
from services.csv_inspector import CSVInspectionError, inspect_csv
from services.result_filter import ResultProcessingError, filter_verified_results
from services.storage import BlobStorageBackend, LocalStorageBackend, StorageBackend, StorageError
from services.veriphone_client import VeriphoneAPIError, VeriphoneClient

APP_ROOT = Path(__file__).resolve().parent
TMP_ROOT = APP_ROOT / ".tmp"
DEFAULT_COUNTRY = "US"
MAX_FILE_SIZE_MB = 1024
LARGE_TRANSFER_THRESHOLD_BYTES = 4_500_000


class ConfigurationError(RuntimeError):
    """Raised when required runtime configuration is missing."""


class JobNotReadyError(RuntimeError):
    """Raised when a finished-looking job still needs a moment to finalize its export."""


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE_MB * 1024 * 1024

    load_dotenv(APP_ROOT / ".env")
    if not _blob_upload_enabled():
        TMP_ROOT.mkdir(parents=True, exist_ok=True)

    @app.get("/")
    def index() -> str:
        return render_template(
            "index.html",
            default_country_label="USA",
            max_file_size_mb=MAX_FILE_SIZE_MB,
            client_upload_enabled=_blob_upload_enabled(),
        )

    @app.get("/favicon.ico")
    def favicon() -> tuple[str, int]:
        return ("", 204)

    @app.post("/api/uploads/inspect")
    def inspect_upload() -> Any:
        try:
            storage = _storage_backend()
            if request.is_json:
                return _inspect_blob_upload(storage)
            return _inspect_form_upload(storage)
        except CSVInspectionError as error:
            return _api_error(str(error), 400)
        except CSVNormalizationError as error:
            return _api_error(str(error), 400)
        except StorageError as error:
            return _api_error(str(error), 500)
        except FileNotFoundError:
            return _api_error("The uploaded CSV could not be found. Upload it again and retry.", 404)
        except Exception as error:  # pragma: no cover - defensive fallback
            return _api_error(f"Could not inspect the CSV upload: {error}", 500)

    @app.post("/api/jobs")
    def start_job() -> Any:
        payload = request.get_json(silent=True) or {}

        try:
            storage = _storage_backend()
            upload_id = str(payload.get("upload_id", "")).strip()
            if not upload_id:
                return _api_error("Missing upload_id.", 400)

            upload_manifest = storage.read_json(_upload_manifest_key(upload_id))
            inspection = upload_manifest["inspection"]
            column_index = _require_int(payload.get("phone_column_index"), "phone_column_index")
            first_data_row = _require_int(payload.get("first_data_row"), "first_data_row")

            columns = inspection["columns"]
            if column_index < 0 or column_index >= len(columns):
                return _api_error("phone_column_index is outside the CSV column range.", 400)

            with tempfile.TemporaryDirectory() as tmpdir:
                normalized_path = Path(tmpdir) / "normalized.csv"
                storage.download_file(str(upload_manifest["stored_path"]), normalized_path)

                client = _build_client()
                remote_upload = client.upload_file(
                    csv_path=normalized_path,
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
            storage.write_json(_job_manifest_key(job_id), job_record)

            refreshed_job = _refresh_job(job_record, client, storage)
            storage.write_json(_job_manifest_key(job_id), refreshed_job)
            return jsonify(_serialize_job(refreshed_job))
        except FileNotFoundError:
            return _api_error("The uploaded CSV could not be found. Re-upload the file and try again.", 404)
        except ConfigurationError as error:
            return _api_error(str(error), 500)
        except StorageError as error:
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
            storage = _storage_backend()
            job_record = storage.read_json(_job_manifest_key(job_id))
            needs_refresh = job_record["status"] not in {"completed", "error"} or (
                job_record["status"] == "completed" and not job_record.get("export_path")
            )
            if needs_refresh:
                job_record = _refresh_and_persist_job(job_record, storage)

            return jsonify(_serialize_job(job_record))
        except FileNotFoundError:
            return _api_error("Job not found.", 404)
        except ConfigurationError as error:
            return _api_error(str(error), 500)
        except StorageError as error:
            return _api_error(str(error), 500)
        except VeriphoneAPIError as error:
            storage = _storage_backend()
            job_record = _safe_read_json(storage, _job_manifest_key(job_id))
            if job_record:
                job_record["status"] = "error"
                job_record["error"] = error.message
                storage.write_json(_job_manifest_key(job_id), job_record)
            return _api_error(error.message, error.status_code)
        except ResultProcessingError as error:
            storage = _storage_backend()
            job_record = _safe_read_json(storage, _job_manifest_key(job_id))
            if job_record:
                job_record["status"] = "error"
                job_record["error"] = str(error)
                storage.write_json(_job_manifest_key(job_id), job_record)
            return _api_error(str(error), 500)
        except Exception as error:  # pragma: no cover - defensive fallback
            return _api_error(f"Could not fetch job status: {error}", 500)

    @app.get("/api/jobs/<job_id>/download")
    def download_filtered_file(job_id: str) -> Any:
        try:
            storage = _storage_backend()
            job_record = storage.read_json(_job_manifest_key(job_id))
            attempted_refresh = False

            while True:
                job_record, refreshed = _ensure_job_downloadable(job_record, storage)
                attempted_refresh = attempted_refresh or refreshed

                try:
                    return _build_download_response(job_record, storage)
                except JobNotReadyError as error:
                    return _api_error(str(error), 409)
                except FileNotFoundError as error:
                    if attempted_refresh or job_record.get("status") == "error":
                        return _api_error(
                            "The filtered CSV is still finalizing. Retry in a few seconds.",
                            409,
                        )

                    job_record = _refresh_and_persist_job(job_record, storage)
                    attempted_refresh = True
                except StorageError as error:
                    if attempted_refresh or job_record.get("status") == "error":
                        raise error

                    job_record = _refresh_and_persist_job(job_record, storage)
                    attempted_refresh = True
        except FileNotFoundError:
            return _api_error("Job not found.", 404)
        except ConfigurationError as error:
            return _api_error(str(error), 500)
        except VeriphoneAPIError as error:
            return _api_error(error.message, error.status_code)
        except ResultProcessingError as error:
            return _api_error(str(error), 500)
        except StorageError as error:
            return _api_error(str(error), 500)

    return app


def _inspect_form_upload(storage: StorageBackend) -> Any:
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return _api_error("Choose a CSV file before uploading.", 400)

    filename = secure_filename(upload.filename)
    if not filename.lower().endswith(".csv"):
        return _api_error("Only CSV uploads are supported.", 400)

    upload_id = uuid.uuid4().hex
    raw_key = _raw_upload_key(upload_id, filename)
    normalized_key = _normalized_upload_key(upload_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        raw_path = Path(tmpdir) / filename
        normalized_path = Path(tmpdir) / "normalized.csv"
        upload.save(raw_path)
        normalize_csv_for_veriphone(raw_path, normalized_path)
        inspection = inspect_csv(normalized_path)

        storage.upload_file(
            raw_path,
            raw_key,
            content_type="text/csv",
            multipart=_should_use_multipart(raw_path),
        )
        storage.upload_file(
            normalized_path,
            normalized_key,
            content_type="text/csv",
            multipart=_should_use_multipart(normalized_path),
        )

    manifest = {
        "upload_id": upload_id,
        "filename": filename,
        "raw_storage_key": raw_key,
        "stored_path": normalized_key,
        "inspection": inspection,
    }
    storage.write_json(_upload_manifest_key(upload_id), manifest)

    return jsonify(
        {
            "status": "success",
            "upload_id": upload_id,
            "filename": filename,
            **inspection,
        }
    )


def _inspect_blob_upload(storage: StorageBackend) -> Any:
    if not storage.is_blob:
        return _api_error("Browser direct uploads are not enabled in this local run.", 400)

    payload = request.get_json(silent=True) or {}
    blob_pathname = str(payload.get("blob_pathname", "")).strip()
    filename = secure_filename(str(payload.get("filename", "")).strip() or Path(blob_pathname).name)

    if not blob_pathname:
        return _api_error("The uploaded blob reference is missing.", 400)
    if not filename.lower().endswith(".csv"):
        return _api_error("Only CSV uploads are supported.", 400)

    upload_id = uuid.uuid4().hex
    normalized_key = _normalized_upload_key(upload_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        raw_path = Path(tmpdir) / filename
        normalized_path = Path(tmpdir) / "normalized.csv"
        storage.download_file(blob_pathname, raw_path)
        normalize_csv_for_veriphone(raw_path, normalized_path)
        inspection = inspect_csv(normalized_path)
        storage.upload_file(
            normalized_path,
            normalized_key,
            content_type="text/csv",
            multipart=_should_use_multipart(normalized_path),
        )

    manifest = {
        "upload_id": upload_id,
        "filename": filename,
        "raw_storage_key": blob_pathname,
        "stored_path": normalized_key,
        "inspection": inspection,
    }
    storage.write_json(_upload_manifest_key(upload_id), manifest)

    return jsonify(
        {
            "status": "success",
            "upload_id": upload_id,
            "filename": filename,
            **inspection,
        }
    )


def _refresh_job(
    job_record: Dict[str, Any],
    client: VeriphoneClient,
    storage: StorageBackend | None = None,
) -> Dict[str, Any]:
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
            storage_backend = storage or _storage_backend()
            export_name = f"{Path(job_record['filename']).stem}_mobile_only.csv"
            export_key = _export_key(job_record["job_id"], export_name)

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir_path = Path(tmpdir)
                verified_path = tmpdir_path / "veriphone_results.csv"
                original_path = tmpdir_path / "normalized.csv"
                export_path = tmpdir_path / export_name

                client.download_results(remote_id, verified_path)
                storage_backend.download_file(str(job_record["original_file_path"]), original_path)
                summary = filter_verified_results(
                    original_csv_path=original_path,
                    verified_csv_path=verified_path,
                    output_csv_path=export_path,
                    has_header=bool(job_record["inspection"]["has_header"]),
                    original_column_labels=[
                        str(column["label"]) for column in job_record["inspection"]["columns"]
                    ],
                )
                storage_backend.upload_file(
                    export_path,
                    export_key,
                    content_type="text/csv",
                    multipart=_should_use_multipart(export_path),
                )

            summary["output_file"] = export_name
            job_record["export_path"] = export_key
            job_record["export_filename"] = export_name
            job_record["summary"] = summary

        job_record["status"] = "completed"
        return job_record

    if remote_status.lower() == "deleted":
        job_record["status"] = "error"
        job_record["error"] = "The remote Veriphone upload is no longer available."
        return job_record

    job_record["status"] = "verifying"
    return job_record


def _refresh_and_persist_job(job_record: Dict[str, Any], storage: StorageBackend) -> Dict[str, Any]:
    refreshed_job = _refresh_job(job_record, _build_client(), storage)
    storage.write_json(_job_manifest_key(refreshed_job["job_id"]), refreshed_job)
    return refreshed_job


def _ensure_job_downloadable(
    job_record: Dict[str, Any],
    storage: StorageBackend,
) -> tuple[Dict[str, Any], bool]:
    export_path = str(job_record.get("export_path", "")).strip()
    if export_path or job_record.get("status") == "error":
        return job_record, False

    refreshed_job = _refresh_and_persist_job(job_record, storage)
    return refreshed_job, True


def _build_download_response(job_record: Dict[str, Any], storage: StorageBackend) -> Response:
    export_path = str(job_record.get("export_path", "")).strip()
    if not export_path:
        raise JobNotReadyError("The filtered CSV is still finalizing. Retry in a few seconds.")

    download_name = str(job_record.get("export_filename") or Path(export_path).name)
    if isinstance(storage, LocalStorageBackend):
        file_path = storage.resolve_path(export_path)
        if not file_path.exists():
            raise FileNotFoundError(export_path)

        return send_file(
            file_path,
            as_attachment=True,
            download_name=download_name,
            mimetype="text/csv",
        )

    stream, metadata = storage.stream_file(export_path)
    headers = {
        "Content-Disposition": f'attachment; filename="{download_name}"',
        "Cache-Control": str(metadata.get("cache_control", "private, no-store")),
    }
    if metadata.get("content_length") is not None:
        headers["Content-Length"] = str(metadata["content_length"])

    return Response(
        stream_with_context(stream),
        mimetype=str(metadata.get("content_type") or "text/csv"),
        headers=headers,
    )


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
            "VERIPHONE_API_KEY is missing. Add it to the deployment environment before running jobs."
        )
    return VeriphoneClient(api_key=api_key)


@lru_cache(maxsize=1)
def _storage_backend() -> StorageBackend:
    if _blob_upload_enabled():
        return BlobStorageBackend()
    return LocalStorageBackend(TMP_ROOT)


def _blob_upload_enabled() -> bool:
    return bool(os.getenv("BLOB_READ_WRITE_TOKEN", "").strip())


def _raw_upload_key(upload_id: str, filename: str) -> str:
    return f"uploads/{upload_id}/raw/{filename}"


def _normalized_upload_key(upload_id: str) -> str:
    return f"uploads/{upload_id}/normalized.csv"


def _upload_manifest_key(upload_id: str) -> str:
    return f"uploads/{upload_id}/manifest.json"


def _job_manifest_key(job_id: str) -> str:
    return f"jobs/{job_id}.json"


def _export_key(job_id: str, filename: str) -> str:
    return f"exports/{job_id}/{filename}"


def _safe_read_json(storage: StorageBackend, storage_path: str) -> Dict[str, Any] | None:
    try:
        return storage.read_json(storage_path)
    except FileNotFoundError:
        return None


def _require_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be an integer.") from error


def _should_use_multipart(path: Path) -> bool:
    return Path(path).stat().st_size > LARGE_TRANSFER_THRESHOLD_BYTES


def _api_error(message: str, status_code: int) -> Any:
    response = jsonify({"status": "error", "message": message})
    response.status_code = status_code
    return response


app = create_app()


if __name__ == "__main__":
    debug_enabled = os.getenv("VERIPHONE_DEBUG", "").strip() == "1"
    app.run(host="127.0.0.1", port=5000, debug=debug_enabled)
