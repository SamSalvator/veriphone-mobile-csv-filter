from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from services.result_filter import ResultProcessingError, filter_verified_results


class ResultFilterTests(unittest.TestCase):
    def test_filters_to_mobile_rows_and_appends_audit_columns(self) -> None:
        original_path = self._write_csv(
            [
                ["name", "phone", "company"],
                ["Alice", "+14155550123", "Northwind"],
                ["Bob", "+14155550124", "Contoso"],
                ["Cara", "+14155550125", "Fabrikam"],
            ]
        )
        verified_path = self._write_csv(
            [
                ["status", "phone_valid", "line_type", "e164", "carrier", "country_code"],
                ["success", "true", "mobile", "+14155550123", "Carrier A", "US"],
                ["success", "true", "fixed_line", "+14155550124", "Carrier B", "US"],
                ["error", "false", "", "", "", ""],
            ]
        )
        output_path = Path(tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name)
        self.addCleanup(lambda: output_path.unlink(missing_ok=True))

        summary = filter_verified_results(
            original_csv_path=original_path,
            verified_csv_path=verified_path,
            output_csv_path=output_path,
            has_header=True,
            original_column_labels=["name", "phone", "company"],
        )

        with output_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))

        self.assertEqual(summary["mobile_rows"], 1)
        self.assertEqual(rows[0][-6:], [
            "veriphone_status",
            "veriphone_phone_valid",
            "veriphone_phone_type",
            "veriphone_e164",
            "veriphone_carrier",
            "veriphone_country_code",
        ])
        self.assertEqual(rows[1][0:3], ["Alice", "+14155550123", "Northwind"])
        self.assertEqual(rows[1][-6:], ["success", "true", "mobile", "+14155550123", "Carrier A", "US"])
        self.assertEqual(len(rows), 2)

    def test_raises_when_phone_type_column_is_missing(self) -> None:
        original_path = self._write_csv(
            [
                ["name", "phone"],
                ["Alice", "+14155550123"],
            ]
        )
        verified_path = self._write_csv(
            [
                ["status", "phone_valid"],
                ["success", "true"],
            ]
        )
        output_path = Path(tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name)
        self.addCleanup(lambda: output_path.unlink(missing_ok=True))

        with self.assertRaises(ResultProcessingError):
            filter_verified_results(
                original_csv_path=original_path,
                verified_csv_path=verified_path,
                output_csv_path=output_path,
                has_header=True,
                original_column_labels=["name", "phone"],
            )

    def test_tolerates_malformed_original_fields_in_veriphone_download(self) -> None:
        original_path = self._write_csv(
            [
                ["Id", "Contact Person", "Phone"],
                ["1", 'Ms. Elizabeth W. Beth Black (President) , Mr. John E. Jeb Black (Vice President)', "(919) 755-0864"],
            ]
        )
        verified_path = Path(tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name)
        self.addCleanup(lambda: verified_path.unlink(missing_ok=True))
        verified_path.write_text(
            (
                "Id,Contact Person,Phone,phone_valid,e164,local_number,type,country,region,carrier\n"
                '1,"Ms. Elizabeth W. Beth Black (President) """," Mr. John E. Jeb"" Black (Vice President)""""",(919) 755-0864,false,,,,,,\n'
            ),
            encoding="utf-8",
        )
        output_path = Path(tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name)
        self.addCleanup(lambda: output_path.unlink(missing_ok=True))

        summary = filter_verified_results(
            original_csv_path=original_path,
            verified_csv_path=verified_path,
            output_csv_path=output_path,
            has_header=True,
            original_column_labels=["Id", "Contact Person", "Phone"],
        )

        with output_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))

        self.assertEqual(summary["total_rows"], 1)
        self.assertEqual(summary["mobile_rows"], 0)
        self.assertEqual(rows[0][-6:], [
            "veriphone_status",
            "veriphone_phone_valid",
            "veriphone_phone_type",
            "veriphone_e164",
            "veriphone_carrier",
            "veriphone_country_code",
        ])
        self.assertEqual(len(rows), 1)

    def test_tolerates_malformed_original_row_during_merge(self) -> None:
        original_path = Path(tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name)
        self.addCleanup(lambda: original_path.unlink(missing_ok=True))
        original_path.write_text(
            (
                "Id,Contact Person,Phone\n"
                '1,"Ms. Elizabeth W. Beth Black (President) """," Mr. John E. Jeb"" Black (Vice President)""""",(919) 755-0864\n'
            ),
            encoding="utf-8",
        )
        verified_path = self._write_csv(
            [
                ["Id", "Contact Person", "Phone", "phone_valid", "e164", "local_number", "type", "country", "region", "carrier"],
                ["1", "Merged contact", "(919) 755-0864", "true", "+19197550864", "(919) 755-0864", "mobile", "United States", "North Carolina", "Carrier A"],
            ]
        )
        output_path = Path(tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name)
        self.addCleanup(lambda: output_path.unlink(missing_ok=True))

        summary = filter_verified_results(
            original_csv_path=original_path,
            verified_csv_path=verified_path,
            output_csv_path=output_path,
            has_header=True,
            original_column_labels=["Id", "Contact Person", "Phone"],
        )

        with output_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))

        self.assertEqual(summary["mobile_rows"], 1)
        self.assertEqual(rows[1][0], "1")
        self.assertEqual(rows[1][2], "(919) 755-0864")
        self.assertEqual(rows[1][-6:], ["", "true", "mobile", "+19197550864", "Carrier A", "United States"])

    def _write_csv(self, rows: list[list[str]]) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
        with handle:
            writer = csv.writer(handle)
            writer.writerows(rows)
        path = Path(handle.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path


if __name__ == "__main__":
    unittest.main()
