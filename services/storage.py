from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator

import requests


class StorageError(RuntimeError):
    """Raised when the configured storage backend cannot complete an operation."""


class StorageBackend:
    """Simple file/json storage contract shared by local and Vercel backends."""

    is_blob = False

    def upload_file(
        self,
        local_path: Path,
        storage_path: str,
        *,
        content_type: str,
        multipart: bool = False,
        overwrite: bool = True,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def download_file(self, storage_path: str, destination: Path) -> Path:
        raise NotImplementedError

    def write_json(self, storage_path: str, payload: Dict[str, Any]) -> None:
        raise NotImplementedError

    def read_json(self, storage_path: str) -> Dict[str, Any]:
        raise NotImplementedError

    def stream_file(self, storage_path: str) -> tuple[Iterator[bytes], Dict[str, Any]]:
        raise NotImplementedError


class LocalStorageBackend(StorageBackend):
    """Persists data under the project's .tmp directory for local development."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve_path(self, storage_path: str) -> Path:
        return self.root / storage_path

    def upload_file(
        self,
        local_path: Path,
        storage_path: str,
        *,
        content_type: str,
        multipart: bool = False,
        overwrite: bool = True,
    ) -> Dict[str, Any]:
        del multipart

        source = Path(local_path)
        destination = self.resolve_path(storage_path)
        if destination.exists() and not overwrite:
            raise StorageError(f"{storage_path} already exists.")

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return {
            "pathname": storage_path,
            "content_type": content_type,
            "size": destination.stat().st_size,
        }

    def download_file(self, storage_path: str, destination: Path) -> Path:
        source = self.resolve_path(storage_path)
        if not source.exists():
            raise FileNotFoundError(storage_path)

        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return destination

    def write_json(self, storage_path: str, payload: Dict[str, Any]) -> None:
        destination = self.resolve_path(storage_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def read_json(self, storage_path: str) -> Dict[str, Any]:
        source = self.resolve_path(storage_path)
        if not source.exists():
            raise FileNotFoundError(storage_path)
        return json.loads(source.read_text(encoding="utf-8"))

    def stream_file(self, storage_path: str) -> tuple[Iterator[bytes], Dict[str, Any]]:
        source = self.resolve_path(storage_path)
        if not source.exists():
            raise FileNotFoundError(storage_path)

        def generate() -> Iterator[bytes]:
            with source.open("rb") as handle:
                while True:
                    chunk = handle.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk

        return generate(), {
            "content_type": "text/csv",
            "content_length": source.stat().st_size,
        }


class BlobStorageBackend(StorageBackend):
    """Persists files and manifests in Vercel Blob for stateless deployments."""

    is_blob = True

    def __init__(self) -> None:
        token = os.getenv("BLOB_READ_WRITE_TOKEN", "").strip()
        if not token:
            raise StorageError(
                "BLOB_READ_WRITE_TOKEN is missing. Connect a Vercel Blob store to this project."
            )

        try:
            from vercel.blob.client import BlobClient
            from vercel.blob.errors import BlobError, BlobNotFoundError
        except Exception as error:  # pragma: no cover - only exercised in Vercel runtime
            raise StorageError(
                "The Vercel Blob SDK is unavailable. Ensure the deployment installs the `vercel` package."
            ) from error

        self.token = token
        self.client = BlobClient(token=token)
        self._blob_error_type = BlobError
        self._not_found_type = BlobNotFoundError

    def upload_file(
        self,
        local_path: Path,
        storage_path: str,
        *,
        content_type: str,
        multipart: bool = False,
        overwrite: bool = True,
    ) -> Dict[str, Any]:
        try:
            uploaded = self.client.upload_file(
                local_path,
                storage_path,
                access="private",
                content_type=content_type,
                overwrite=overwrite,
                multipart=multipart,
            )
        except self._blob_error_type as error:
            raise StorageError(str(error)) from error

        return {
            "pathname": uploaded.pathname,
            "url": uploaded.url,
            "download_url": uploaded.download_url,
            "content_type": uploaded.content_type,
        }

    def download_file(self, storage_path: str, destination: Path) -> Path:
        try:
            return Path(self.client.download_file(storage_path, destination))
        except self._not_found_type as error:
            raise FileNotFoundError(storage_path) from error
        except self._blob_error_type as error:
            raise StorageError(str(error)) from error

    def write_json(self, storage_path: str, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        try:
            self.client.put(
                storage_path,
                body,
                access="private",
                content_type="application/json",
                overwrite=True,
            )
        except self._blob_error_type as error:
            raise StorageError(str(error)) from error

    def read_json(self, storage_path: str) -> Dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmpdir:
            destination = Path(tmpdir) / "payload.json"
            downloaded = self.download_file(storage_path, destination)
            return json.loads(downloaded.read_text(encoding="utf-8"))

    def stream_file(self, storage_path: str) -> tuple[Iterator[bytes], Dict[str, Any]]:
        try:
            metadata = self.client.head(storage_path)
        except self._not_found_type as error:
            raise FileNotFoundError(storage_path) from error
        except self._blob_error_type as error:
            raise StorageError(str(error)) from error

        response = requests.get(
            metadata.url,
            headers={"Authorization": f"Bearer {self.token}"},
            stream=True,
            timeout=120,
        )
        try:
            response.raise_for_status()
        except requests.RequestException as error:
            response.close()
            raise StorageError(f"Could not stream {storage_path} from Vercel Blob.") from error

        def generate() -> Iterator[bytes]:
            try:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        yield chunk
            finally:
                response.close()

        return generate(), {
            "content_type": metadata.content_type,
            "content_length": metadata.size,
            "cache_control": "private, no-store",
        }
