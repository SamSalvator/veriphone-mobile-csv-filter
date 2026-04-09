from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Callable, Dict, Iterable, List

import pandas as pd

KNOWN_PHONE_TYPES = {
    "",
    "mobile",
    "fixed_line",
    "toll_free",
    "premium_rate",
    "shared_cost",
    "voip",
    "unknown",
}

AUDIT_ALIAS_MAP = {
    "veriphone_status": ["veriphone_status", "result_status", "status"],
    "veriphone_phone_valid": ["veriphone_phone_valid", "phone_valid", "valid"],
    "veriphone_phone_type": ["veriphone_phone_type", "phone_type", "line_type", "type"],
    "veriphone_e164": ["veriphone_e164", "e164", "e_164", "international_number"],
    "veriphone_carrier": ["veriphone_carrier", "carrier", "phone_carrier"],
    "veriphone_country_code": ["veriphone_country_code", "country_code", "countrycode"],
}


class ResultProcessingError(Exception):
    """Raised when a Veriphone result CSV cannot be normalized safely."""


def filter_verified_results(
    original_csv_path: Path,
    verified_csv_path: Path,
    output_csv_path: Path,
    has_header: bool,
    original_column_labels: List[str],
) -> Dict[str, object]:
    original_df = _read_original_dataframe(original_csv_path, has_header, original_column_labels)
    verified_df = _read_verified_dataframe(
        verified_csv_path,
        original_column_count=len(original_column_labels),
    )

    if len(original_df.index) != len(verified_df.index):
        raise ResultProcessingError(
            "The Veriphone results CSV does not line up with the original CSV row count."
        )

    audit_series = {
        field_name: _extract_audit_series(verified_df, field_name)
        for field_name in AUDIT_ALIAS_MAP
    }

    phone_type = audit_series["veriphone_phone_type"].str.strip().str.lower()
    mobile_mask = phone_type.eq("mobile")
    filtered_df = original_df.loc[mobile_mask].copy()

    for field_name, series in audit_series.items():
        filtered_df[field_name] = series.loc[mobile_mask].tolist()

    output_csv_path = Path(output_csv_path)
    output_csv_path.parent.mkdir(parents=True, exist_ok=True)
    filtered_df.to_csv(output_csv_path, index=False)

    return {
        "total_rows": int(len(original_df.index)),
        "mobile_rows": int(mobile_mask.sum()),
        "excluded_rows": int(len(original_df.index) - mobile_mask.sum()),
        "output_file": str(output_csv_path),
    }


def _read_original_dataframe(
    csv_path: Path,
    has_header: bool,
    original_column_labels: List[str],
) -> pd.DataFrame:
    csv_path = Path(csv_path)
    expected_column_count = len(original_column_labels)
    with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        raw_rows = [
            row for row in reader
            if any(cell.strip() for cell in row)
        ]

    if has_header:
        raw_rows = raw_rows[1:]

    repaired_rows = [
        _repair_row_width(row, original_column_labels)
        for row in raw_rows
    ]
    dataframe = pd.DataFrame(repaired_rows, columns=original_column_labels).fillna("")
    if len(dataframe.columns) != expected_column_count:
        raise ResultProcessingError(
            "The original CSV columns no longer match the inspected upload metadata."
        )
    return dataframe


def _read_verified_dataframe(csv_path: Path, original_column_count: int) -> pd.DataFrame:
    csv_path = Path(csv_path)
    with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header_row = next(reader)
        except StopIteration as error:
            raise ResultProcessingError("The downloaded Veriphone results CSV is empty.") from error

        if not header_row:
            raise ResultProcessingError("The downloaded Veriphone results CSV is empty.")

        verification_headers = _select_verification_headers(header_row, original_column_count)
        verification_rows = [
            _extract_verification_tail(row, len(verification_headers))
            for row in reader
        ]

    dataframe = pd.DataFrame(verification_rows, columns=verification_headers).fillna("")
    if dataframe.empty and len(dataframe.columns) == 0:
        raise ResultProcessingError("The downloaded Veriphone results CSV is empty.")
    return dataframe


def _select_verification_headers(header_row: List[str], original_column_count: int) -> List[str]:
    if len(header_row) > original_column_count:
        tail_headers = header_row[original_column_count:]
        full_score = _count_audit_header_matches(header_row)
        tail_score = _count_audit_header_matches(tail_headers)
        if tail_score >= full_score and tail_score > 0:
            return tail_headers
        if full_score > 0:
            return header_row
        return tail_headers

    if _count_audit_header_matches(header_row) > 0:
        return header_row

    return header_row


def _extract_verification_tail(row: List[str], verification_column_count: int) -> List[str]:
    if verification_column_count <= 0:
        return []

    if len(row) >= verification_column_count:
        return row[-verification_column_count:]

    return row + [""] * (verification_column_count - len(row))


def _count_audit_header_matches(headers: List[str]) -> int:
    aliases = {
        _normalize(alias)
        for values in AUDIT_ALIAS_MAP.values()
        for alias in values
    }
    return sum(1 for header in headers if _normalize(header) in aliases)


def _repair_row_width(row: List[str], expected_headers: List[str]) -> List[str]:
    expected_count = len(expected_headers)
    if len(row) == expected_count:
        return row

    if len(row) < expected_count:
        return row + [""] * (expected_count - len(row))

    repaired = list(row)
    while len(repaired) > expected_count:
        repaired = _best_adjacent_merge(repaired, expected_headers)

    return repaired


def _best_adjacent_merge(row: List[str], expected_headers: List[str]) -> List[str]:
    best_score = float("-inf")
    best_candidate = row[:-1]

    for merge_index in range(len(row) - 1):
        merged_value = f"{row[merge_index]},{row[merge_index + 1]}"
        candidate = row[:merge_index] + [merged_value] + row[merge_index + 2:]
        score = _score_row_candidate(candidate, expected_headers)
        if score > best_score:
            best_score = score
            best_candidate = candidate

    return best_candidate


def _score_row_candidate(candidate: List[str], expected_headers: List[str]) -> float:
    score = 0.0
    for header, value in zip(expected_headers, candidate):
        normalized_header = _normalize(header)
        stripped_value = value.strip()
        lowered_value = stripped_value.lower()

        if not stripped_value:
            score += 0.1
            continue

        if any(token in normalized_header for token in ["phone_type", "line_type"]):
            score += 2.5 if lowered_value in KNOWN_PHONE_TYPES else -2.0
            continue

        if "phone" in normalized_header and "type" not in normalized_header and "country_code" not in normalized_header:
            score += 3.0 if _looks_phoneish_value(stripped_value) else -2.0
            continue

        if any(token in normalized_header for token in ["website", "url", "link"]):
            score += 2.0 if stripped_value.startswith(("http://", "https://")) else -1.0
            continue

        if "email" in normalized_header:
            score += 2.0 if "@" in stripped_value else -1.0
            continue

        if normalized_header == "state":
            score += 2.0 if len(stripped_value) == 2 and stripped_value.isalpha() else -1.0
            continue

        if any(token in normalized_header for token in ["zip", "postal"]):
            score += 1.5 if re.fullmatch(r"[\d-]+", stripped_value) else -1.0
            continue

        if normalized_header in {"id", "number_of_employees"} or normalized_header.endswith("_count"):
            score += 1.0 if lowered_value == "n/a" or re.fullmatch(r"[\d.]+", stripped_value) else -0.5
            continue

        if '"' in stripped_value:
            score -= 0.15

        score += 0.25

    return score


def _looks_phoneish_value(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return len(digits) >= 7


def _extract_audit_series(dataframe: pd.DataFrame, field_name: str) -> pd.Series:
    column_name = _find_best_column(
        dataframe,
        aliases=AUDIT_ALIAS_MAP[field_name],
        validator=_validator_for(field_name),
    )
    if column_name is None:
        if field_name == "veriphone_phone_type":
            raise ResultProcessingError(
                "Could not find a phone_type-equivalent column in the Veriphone results CSV."
            )
        return pd.Series([""] * len(dataframe.index))

    return dataframe[column_name].astype(str).fillna("")


def _find_best_column(
    dataframe: pd.DataFrame,
    aliases: Iterable[str],
    validator: Callable[[pd.Series], float] | None = None,
) -> str | None:
    normalized_aliases = [_normalize(alias) for alias in aliases]
    candidates = []

    for column in dataframe.columns:
        normalized_column = _normalize(str(column))
        name_score = max((_column_name_score(normalized_column, alias) for alias in normalized_aliases), default=0)
        if name_score <= 0:
            continue

        value_score = validator(dataframe[column]) if validator else 0.0
        candidates.append((name_score + value_score, value_score, name_score, str(column)))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][3]


def _column_name_score(normalized_column: str, normalized_alias: str) -> float:
    if normalized_column == normalized_alias:
        return 2.0
    if normalized_column.startswith(normalized_alias) or normalized_alias.startswith(normalized_column):
        return 1.3
    if normalized_alias in normalized_column or normalized_column in normalized_alias:
        return 1.0
    return 0.0


def _validator_for(field_name: str) -> Callable[[pd.Series], float] | None:
    validators: Dict[str, Callable[[pd.Series], float]] = {
        "veriphone_status": lambda series: _ratio_score(
            series,
            lambda value: value in {"success", "error", "syntax-error"},
        ),
        "veriphone_phone_valid": lambda series: _ratio_score(
            series,
            lambda value: value in {"true", "false", "1", "0", "yes", "no"},
        ),
        "veriphone_phone_type": lambda series: _ratio_score(
            series,
            lambda value: value in KNOWN_PHONE_TYPES,
        ),
        "veriphone_e164": lambda series: _ratio_score(
            series,
            lambda value: value.startswith("+") and len(re.sub(r"\D", "", value)) >= 7,
        ),
        "veriphone_country_code": lambda series: _ratio_score(
            series,
            lambda value: len(value) == 2 and value.isalpha(),
        ),
    }
    return validators.get(field_name)


def _ratio_score(series: pd.Series, matcher: Callable[[str], bool]) -> float:
    values = [str(value).strip().lower() for value in series.tolist() if str(value).strip()]
    if not values:
        return 0.0

    sample_values = values[:20]
    matched = sum(1 for value in sample_values if matcher(value))
    return (matched / len(sample_values)) * 3.0


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
