from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.veriphone_client import VeriphoneAPIError, VeriphoneClient


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, text="", content_chunks=None):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}
        self.text = text
        self.headers = {"Content-Type": "application/json"}
        self._content_chunks = content_chunks or []

    def json(self):
        if isinstance(self._json_body, Exception):
            raise self._json_body
        return self._json_body

    def iter_content(self, chunk_size=8192):
        del chunk_size
        for chunk in self._content_chunks:
            yield chunk


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        if not self.responses:
            raise AssertionError("No fake responses left for request.")
        return self.responses.pop(0)


class VeriphoneClientTests(unittest.TestCase):
    def test_initializes_bearer_auth_header(self) -> None:
        session = FakeSession([])
        VeriphoneClient(api_key="secret-key", session=session)
        self.assertEqual(session.headers["Authorization"], "Bearer secret-key")

    def test_upload_verify_and_download_use_expected_requests(self) -> None:
        session = FakeSession(
            [
                FakeResponse(json_body={"result": "success", "id": "upload-123"}),
                FakeResponse(json_body={"status": "success", "id": "upload-123"}),
                FakeResponse(content_chunks=[b"header\n", b"value\n"]),
            ]
        )
        client = VeriphoneClient(api_key="secret-key", session=session)

        csv_path = self._write_temp_file("phone\n+14155550123\n")
        destination = Path(tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name)
        self.addCleanup(lambda: destination.unlink(missing_ok=True))

        client.upload_file(csv_path, column=0, firstrow=1)
        client.start_verification("upload-123", default_country="US")
        client.download_results("upload-123", destination)

        self.assertEqual(session.calls[0]["method"], "POST")
        self.assertTrue(session.calls[0]["url"].endswith("/v2/file/upload"))
        self.assertEqual(session.calls[0]["kwargs"]["data"], {"column": "0", "firstrow": "1"})
        self.assertEqual(session.calls[1]["kwargs"]["params"]["default_country"], "US")
        self.assertTrue(session.calls[2]["url"].endswith("/v2/file/download"))
        self.assertEqual(destination.read_text(encoding="utf-8"), "header\nvalue\n")

    def test_error_response_raises_veriphone_api_error(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    status_code=402,
                    json_body={
                        "status": "error",
                        "code": 402,
                        "type": "PaymentRequired",
                        "message": "Insufficient credits",
                    },
                )
            ]
        )
        client = VeriphoneClient(api_key="secret-key", session=session)

        with self.assertRaises(VeriphoneAPIError) as captured:
            client.get_status("upload-123")

        self.assertEqual(captured.exception.status_code, 402)
        self.assertEqual(captured.exception.message, "Insufficient credits")

    def _write_temp_file(self, content: str) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        with handle:
            handle.write(content)
        path = Path(handle.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path


if __name__ == "__main__":
    unittest.main()
