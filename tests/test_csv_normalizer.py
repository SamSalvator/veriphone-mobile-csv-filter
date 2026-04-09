from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from services.csv_normalizer import normalize_csv_for_veriphone


class CSVNormalizerTests(unittest.TestCase):
    def test_normalizer_repairs_stray_quotes_for_veriphone(self) -> None:
        source_path = self._write_text(
            'Id,Contact Person,Phone\n'
            '96,"Mr. Thomas Tim" Gifford (President)",(757) 583-1801\n'
        )
        destination_path = Path(tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name)
        self.addCleanup(lambda: destination_path.unlink(missing_ok=True))

        normalize_csv_for_veriphone(source_path, destination_path)

        with destination_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))

        self.assertEqual(rows[0], ["Id", "Contact Person", "Phone"])
        self.assertEqual(rows[1][0], "96")
        self.assertIn("Thomas Tim Gifford", rows[1][1])
        self.assertEqual(rows[1][2], "(757) 583-1801")

    def _write_text(self, content: str) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
        with handle:
            handle.write(content)
        path = Path(handle.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path


if __name__ == "__main__":
    unittest.main()
