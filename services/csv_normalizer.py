from __future__ import annotations

import csv
from pathlib import Path


class CSVNormalizationError(Exception):
    """Raised when a CSV cannot be normalized into a canonical format."""


def normalize_csv_for_veriphone(source_path: Path, destination_path: Path) -> Path:
    """Rewrite an uploaded CSV into a clean canonical CSV for Veriphone.

    Python's CSV reader is more tolerant than Veriphone's bulk parser for malformed
    quoting, so we read the full file once and write it back out with standard
    quoting. This preserves row order and column order while fixing issues like
    stray quotes inside cells.
    """

    source_path = Path(source_path)
    destination_path = Path(destination_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with source_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as source_handle:
            reader = csv.reader(source_handle)
            with destination_path.open("w", encoding="utf-8", newline="") as destination_handle:
                writer = csv.writer(destination_handle, quoting=csv.QUOTE_MINIMAL)
                for row in reader:
                    writer.writerow(row)
    except (OSError, csv.Error) as error:
        raise CSVNormalizationError(f"Could not normalize the uploaded CSV: {error}") from error

    return destination_path
