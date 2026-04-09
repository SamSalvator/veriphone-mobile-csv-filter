from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import requests

DEFAULT_BASE_URL = "https://api.veriphone.io"


@dataclass
class VeriphoneAPIError(Exception):
    message: str
    status_code: int = 500
    error_type: str | None = None

    def __str__(self) -> str:
        return self.message


class VeriphoneClient:
    """Thin client over Veriphone's public API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.setdefault("Authorization", f"Bearer {api_key}")
        self.session.headers.setdefault("Accept", "application/json")

    def upload_file(self, csv_path: Path, column: int, firstrow: int) -> Dict[str, Any]:
        csv_path = Path(csv_path)
        with csv_path.open("rb") as handle:
            files = {"file": (csv_path.name, handle, "text/csv")}
            data = {"column": str(column), "firstrow": str(firstrow)}
            return self._request_json("POST", "/v2/file/upload", files=files, data=data)

    def start_verification(
        self,
        upload_id: str,
        default_country: str = "US",
    ) -> Dict[str, Any]:
        params = {"id": upload_id, "default_country": default_country}
        return self._request_json("POST", "/v2/file/verify", params=params)

    def get_status(self, upload_id: str) -> Dict[str, Any]:
        return self._request_json("GET", "/v2/file/status", params={"id": upload_id})

    def get_file_details(self, upload_id: str) -> Dict[str, Any]:
        return self._request_json("GET", "/v2/file/get", params={"id": upload_id})

    def download_results(self, upload_id: str, destination: Path) -> Path:
        destination = Path(destination)
        response = self.session.request(
            "GET",
            f"{self.base_url}/v2/file/download",
            params={"id": upload_id, "as": destination.name},
            timeout=self.timeout,
            stream=True,
        )
        if response.status_code >= 400:
            self._raise_for_error(response)

        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    handle.write(chunk)

        return destination

    def _request_json(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        response = self.session.request(
            method,
            f"{self.base_url}{path}",
            timeout=self.timeout,
            **kwargs,
        )
        if response.status_code >= 400:
            self._raise_for_error(response)

        payload = self._safe_json(response)
        if isinstance(payload, dict) and payload.get("status") == "error":
            raise VeriphoneAPIError(
                message=str(payload.get("message", "Veriphone request failed.")),
                status_code=int(payload.get("code", response.status_code or 500)),
                error_type=str(payload.get("type", "")) or None,
            )

        if isinstance(payload, dict):
            return payload

        raise VeriphoneAPIError(
            "Veriphone returned a non-JSON response.",
            status_code=response.status_code or 500,
        )

    def _raise_for_error(self, response: Any) -> None:
        payload = self._safe_json(response)
        if isinstance(payload, dict):
            message = str(payload.get("message") or payload.get("error") or "Veriphone request failed.")
            status_code = int(payload.get("code") or response.status_code or 500)
            error_type = str(payload.get("type", "")) or None
            raise VeriphoneAPIError(message=message, status_code=status_code, error_type=error_type)

        message = getattr(response, "text", "") or f"Veriphone request failed with status {response.status_code}."
        raise VeriphoneAPIError(message=message, status_code=response.status_code or 500)

    @staticmethod
    def _safe_json(response: Any) -> Dict[str, Any] | Any:
        try:
            return response.json()
        except (ValueError, TypeError):
            text = getattr(response, "text", "")
            try:
                return json.loads(text) if text else {}
            except json.JSONDecodeError:
                return {}
