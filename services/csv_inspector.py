from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

PHONE_HEADER_HINTS = ("phone", "mobile", "cell", "tel", "telephone")


class CSVInspectionError(Exception):
    """Raised when the uploaded CSV cannot be inspected safely."""


def inspect_csv(csv_path: Path, sample_size: int = 5) -> Dict[str, Any]:
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise CSVInspectionError(f"CSV file not found: {csv_path}")

    sample_text = csv_path.read_text(encoding="utf-8-sig", errors="replace")[:8192]
    if not sample_text.strip():
        raise CSVInspectionError("The uploaded CSV is empty.")

    dialect = _detect_dialect(sample_text)
    indexed_rows = _read_non_empty_rows(csv_path, dialect, sample_size + 1)
    if not indexed_rows:
        raise CSVInspectionError("The uploaded CSV does not contain any data rows.")

    row_values = [row for _, row in indexed_rows]
    has_header = _detect_header(sample_text, row_values)
    first_non_empty_row = indexed_rows[0][0]
    first_data_row = first_non_empty_row + (1 if has_header else 0)

    header_row = row_values[0] if has_header else []
    data_rows = row_values[1:] if has_header else row_values
    column_count = max(len(row) for row in row_values)
    labels = _build_column_labels(header_row, column_count, has_header)
    normalized_samples = [_pad_row(row, column_count) for row in data_rows[:sample_size]]

    columns: List[Dict[str, Any]] = []
    for index, label in enumerate(labels):
        sample_values = [row[index] for row in normalized_samples if row[index].strip()][:3]
        columns.append(
            {
                "index": index,
                "label": label,
                "sample_values": sample_values,
                "is_suggested": False,
            }
        )

    suggested_index = _suggest_phone_column(columns, normalized_samples)
    if suggested_index is not None:
        columns[suggested_index]["is_suggested"] = True

    return {
        "has_header": has_header,
        "first_data_row": first_data_row,
        "delimiter": getattr(dialect, "delimiter", ","),
        "columns": columns,
        "sample_rows": normalized_samples,
        "suggested_phone_column_index": suggested_index,
    }


def _detect_dialect(sample_text: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample_text, delimiters=",;\t|")
    except csv.Error:
        return csv.get_dialect("excel")


def _read_non_empty_rows(
    csv_path: Path,
    dialect: csv.Dialect,
    limit: int,
) -> List[Tuple[int, List[str]]]:
    rows: List[Tuple[int, List[str]]] = []
    with csv_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.reader(handle, dialect)
        for row_number, row in enumerate(reader):
            cleaned = [cell.strip() for cell in row]
            if not any(cleaned):
                continue
            rows.append((row_number, cleaned))
            if len(rows) >= limit:
                break
    return rows


def _detect_header(sample_text: str, rows: Sequence[Sequence[str]]) -> bool:
    if not rows:
        return False

    try:
        return bool(csv.Sniffer().has_header(sample_text))
    except csv.Error:
        pass

    if len(rows) < 2:
        return True

    first_row = rows[0]
    second_row = rows[1]
    header_votes = 0
    for index, header_cell in enumerate(first_row):
        candidate_value = second_row[index].strip() if index < len(second_row) else ""
        if _looks_like_header(header_cell) and candidate_value and candidate_value != header_cell:
            header_votes += 1

    return header_votes >= max(1, min(2, len(first_row)))


def _looks_like_header(value: str) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return False
    if "@" in normalized:
        return False
    if _looks_phoneish(normalized):
        return False
    return bool(re.fullmatch(r"[a-z_ /\-]+", normalized))


def _build_column_labels(
    header_row: Sequence[str],
    column_count: int,
    has_header: bool,
) -> List[str]:
    base_labels: List[str] = []
    for index in range(column_count):
        if has_header and index < len(header_row):
            candidate = header_row[index].strip()
        else:
            candidate = ""
        base_labels.append(candidate or f"Column {index + 1}")

    seen: Dict[str, int] = {}
    unique_labels: List[str] = []
    for label in base_labels:
        count = seen.get(label, 0) + 1
        seen[label] = count
        unique_labels.append(label if count == 1 else f"{label} ({count})")

    return unique_labels


def _pad_row(row: Sequence[str], width: int) -> List[str]:
    return list(row) + [""] * max(0, width - len(row))


def _suggest_phone_column(
    columns: Sequence[Dict[str, Any]],
    sample_rows: Sequence[Sequence[str]],
) -> int | None:
    for column in columns:
        label = str(column["label"]).lower()
        if any(hint in label for hint in PHONE_HEADER_HINTS):
            return int(column["index"])

    best_index: int | None = None
    best_score = 0.0
    for column in columns:
        index = int(column["index"])
        values = [row[index] for row in sample_rows if index < len(row)]
        non_empty = [value for value in values if value.strip()]
        if not non_empty:
            continue
        phoneish_count = sum(1 for value in non_empty if _looks_phoneish(value))
        score = phoneish_count / len(non_empty)
        if score > best_score and score >= 0.6:
            best_score = score
            best_index = index

    return best_index


def _looks_phoneish(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) < 7:
        return False
    return bool(re.fullmatch(r"[+\d().\-\s]+", value.strip()))
