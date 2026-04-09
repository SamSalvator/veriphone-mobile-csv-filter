from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.csv_inspector import inspect_csv


class CSVInspectorTests(unittest.TestCase):
    def test_headered_csv_returns_named_columns_and_first_data_row(self) -> None:
        csv_path = self._write_csv("name,phone\nAlice,+14155550123\nBob,+14155550124\n")

        result = inspect_csv(csv_path)

        self.assertTrue(result["has_header"])
        self.assertEqual(result["first_data_row"], 1)
        self.assertEqual(result["columns"][1]["label"], "phone")
        self.assertEqual(result["columns"][1]["sample_values"], ["+14155550123", "+14155550124"])
        self.assertEqual(result["suggested_phone_column_index"], 1)

    def test_headerless_csv_uses_positional_labels(self) -> None:
        csv_path = self._write_csv("+14155550123,Alice\n+14155550124,Bob\n")

        result = inspect_csv(csv_path)

        self.assertFalse(result["has_header"])
        self.assertEqual(result["first_data_row"], 0)
        self.assertEqual(result["columns"][0]["label"], "Column 1")
        self.assertEqual(result["columns"][1]["label"], "Column 2")
        self.assertEqual(result["sample_rows"][0][0], "+14155550123")

    def _write_csv(self, content: str) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        with handle:
            handle.write(content)
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return Path(handle.name)


if __name__ == "__main__":
    unittest.main()
