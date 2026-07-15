"""Explicit parser contract for RBI Bulletin sectoral credit, 2025H2.

This module adds a published-vintage staging model without changing the v1
snapshot parser or the public connector. It reuses v1's bounded HTML-table
engine, numeric policy, stable economic identifiers, and row hierarchy.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Final, Iterable, Sequence
from urllib.parse import urlparse

import pandas as pd

from indiamacro.rbi import sectoral_credit_bulletin as v1


LAYOUT_ID: Final = "RBI_BULLETIN_SECTORAL_CREDIT_V2_2025H2"
PARSER_VERSION: Final = "2.0.0"
SUPPORTED_BULLETIN_PERIODS: Final = (
    "July 2025",
    "August 2025",
    "September 2025",
    "October 2025",
    "November 2025",
    "December 2025",
)

CURRENT_OBSERVATION: Final = "CURRENT_OBSERVATION"
PRIOR_YEAR_REFERENCE: Final = "PRIOR_YEAR_REFERENCE"
FINANCIAL_YEAR_BASE: Final = "FINANCIAL_YEAR_BASE"
PRIOR_PERIOD_REFERENCE: Final = "PRIOR_PERIOD_REFERENCE"
REPORTED_YOY_GROWTH: Final = "REPORTED_YOY_GROWTH"
REPORTED_FINANCIAL_YEAR_GROWTH: Final = "REPORTED_FINANCIAL_YEAR_GROWTH"
COLUMN_ROLES: Final = (
    PRIOR_YEAR_REFERENCE,
    FINANCIAL_YEAR_BASE,
    PRIOR_PERIOD_REFERENCE,
    CURRENT_OBSERVATION,
    REPORTED_FINANCIAL_YEAR_GROWTH,
    REPORTED_YOY_GROWTH,
)

EXACT_CONTINUITY: Final = "EXACT_CONTINUITY"
LIKELY_CONTINUITY_REQUIRES_REVIEW: Final = "LIKELY_CONTINUITY_REQUIRES_REVIEW"
DEFINITION_CHANGED: Final = "DEFINITION_CHANGED"
NEW_SERIES: Final = "NEW_SERIES"
DISCONTINUED_SERIES: Final = "DISCONTINUED_SERIES"
UNRESOLVED: Final = "UNRESOLVED"
CONTINUITY_CLASSES: Final = (
    EXACT_CONTINUITY,
    LIKELY_CONTINUITY_REQUIRES_REVIEW,
    DEFINITION_CHANGED,
    NEW_SERIES,
    DISCONTINUED_SERIES,
    UNRESOLVED,
)

RELEASE_OBSERVATION_COLUMNS: Final = (
    "dataset_id",
    "series_id",
    "source_table",
    "source_row_code",
    "source_label",
    "measure",
    "observation_date",
    "comparison_date",
    "publication_date",
    "bulletin_period",
    "value",
    "unit",
    "population_id",
    "column_role",
    "layout_id",
    "parser_version",
    "source_url",
    "source_sha256",
    "is_provisional",
    "footnote_references",
)
RELEASE_SEMANTIC_COLUMNS: Final = (
    "dataset_id",
    "series_id",
    "source_table",
    "source_row_code",
    "source_label",
    "measure",
    "observation_date",
    "comparison_date",
    "publication_date",
    "bulletin_period",
    "value",
    "unit",
    "population_id",
    "column_role",
    "is_provisional",
    "footnote_references",
)
VINTAGE_KEY_COLUMNS: Final = (
    "dataset_id",
    "series_id",
    "observation_date",
    "measure",
    "column_role",
    "publication_date",
)

MAJOR_SEMANTIC_TITLE: Final = "Deployment of Gross Bank Credit by Major Sectors"
INDUSTRY_SEMANTIC_TITLE: Final = "Industry-wise Deployment of Gross Bank Credit"
TITLE_BY_ROLE: Final = {
    "major": v1.MAJOR_TITLE,
    "industry": v1.INDUSTRY_TITLE,
}
SEMANTIC_TITLE_BY_ROLE: Final = {
    "major": MAJOR_SEMANTIC_TITLE,
    "industry": INDUSTRY_SEMANTIC_TITLE,
}


class V2ContractError(v1.SectoralCreditParseError):
    """The cached release violates the explicit v2 contract."""


class LayoutDetectionError(v1.UnsupportedLayoutError):
    """Positive layout detection found zero supported layouts."""


class AmbiguousLayoutDetectionError(v1.AmbiguousTableError):
    """Positive layout detection matched more than one layout."""


@dataclass(frozen=True)
class RowMappingV2:
    source_table: str
    source_row_code: str
    source_label: str
    normalized_label: str
    parent_row_code: str | None
    sector_level_1: str | None
    sector_level_2: str | None
    sector_level_3: str | None
    is_memorandum: bool
    population_id: str
    base_series_id: str
    continuity_classification: str
    corresponding_v1_base_series_id: str | None
    footnote_references: tuple[str, ...]
    emits_observations: bool


@dataclass(frozen=True)
class ContractSignatures:
    structural_signature_sha256: str
    taxonomy_signature_sha256: str
    methodology_signature_sha256: str


@dataclass(frozen=True)
class ReleaseMetadata:
    dataset_id: str
    layout_id: str
    parser_version: str
    publication_date: str
    bulletin_period: str
    current_observation_date: str
    prior_year_reference_date: str
    financial_year_base_date: str
    section42_prior_year_override_date: str | None
    source_hashes: tuple[tuple[str, str], ...]
    structural_signature_sha256: str
    taxonomy_signature_sha256: str
    methodology_signature_sha256: str
    mapped_row_counts: tuple[tuple[str, int], ...]
    emitted_row_counts: tuple[tuple[str, int], ...]
    supplemental_non_emitting_row_counts: tuple[tuple[str, int], ...]
    ignored_empty_row_counts: tuple[tuple[str, int], ...]
    duplicate_source_column_count: int
    duplicate_source_row_count: int
    growth_reconciliation_checks: int
    growth_reconciliation_skipped: int
    growth_reconciliation_failures: int
    canonical_release_key_duplicate_count: int
    observation_counts_by_measure: tuple[tuple[str, int], ...]
    observation_counts_by_population: tuple[tuple[str, int], ...]
    observation_counts_by_column_role: tuple[tuple[str, int], ...]
    unique_series_count: int
    coverage_percentage: str
    reporting_bank_count: int | None
    release_semantic_sha256: str
    provenance_bound_output_sha256: str


@dataclass(frozen=True)
class ParsedSectoralCreditRelease:
    observations: pd.DataFrame
    metadata: ReleaseMetadata
    notes: tuple[v1.SourceNote, ...]


@dataclass(frozen=True)
class _HeaderDates:
    financial_year_base_date: str
    prior_year_reference_date: str
    prior_period_reference_date: str
    current_observation_date: str
    fiscal_year: str


@dataclass(frozen=True)
class _ContractTable:
    role: str
    published_title: str
    table_ordinal: str | None
    publication_date: str
    bulletin_period: str
    header_rows: tuple[Any, ...]
    data_rows: tuple[Any, ...]
    supplemental_rows: tuple[Any, ...]
    empty_rows: tuple[Any, ...]
    note: str
    note_colspan: int
    source_url: str
    source_sha256: str


@dataclass(frozen=True)
class _ParsedMappedRow:
    mapping: RowMappingV2
    values: tuple[Decimal | None, ...]


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).replace("\xa0", " ").split())


def _normalized_taxonomy_label(source_label: str, source_row_code: str) -> str:
    value = source_label
    value = re.sub(r"[¹²³⁴⁵⁶⁷⁸⁹]", "", value)
    code = re.escape(source_row_code)
    value = re.sub(rf"^{code}\.?\s*", "", value, count=1, flags=re.IGNORECASE)
    value = re.sub(r"\s+[1-7]$", "", value)
    value = re.sub(r"[^0-9A-Za-z&]+", " ", _normalize(value)).casefold()
    return " ".join(value.split())


V2_MAJOR_SOURCE_LABELS: Final = (
    "I. Bank Credit (II + III)",
    "II. Food Credit",
    "III. Non-food Credit",
    "1. Agriculture & Allied Activities",
    "2. Industry (Micro and Small, Medium and Large)",
    "2.1 Micro and Small",
    "2.2 Medium",
    "2.3 Large",
    "3. Services",
    "3.1 Transport Operators",
    "3.2 Computer Software",
    "3.3 Tourism, Hotels & Restaurants",
    "3.4 Shipping",
    "3.5 Aviation",
    "3.6 Professional Services",
    "3.7 Trade",
    "3.7.1. Wholesale Trade¹",
    "3.7.2 Retail Trade",
    "3.8 Commercial Real Estate",
    "3.9 Non-Banking Financial Companies (NBFCs)² of which,",
    "3.9.1 Housing Finance Companies (HFCs)",
    "3.9.2 Public Financial Institutions (PFIs)",
    "3.10 Other Services³",
    "4. Personal Loans",
    "4.1 Consumer Durables",
    "4.2 Housing",
    "4.3 Advances against Fixed Deposits",
    "4.4 Advances to Individuals against share & bonds",
    "4.5 Credit Card Outstanding",
    "4.6 Education",
    "4.7 Vehicle Loans",
    "4.8 Loan against gold jewellery⁴",
    "4.9 Other Personal Loans",
    "5. Priority Sector (Memo)",
    "(i) Agriculture & Allied Activities⁵",
    "(ii) Micro & Small Enterprises⁶",
    "(iii) Medium Enterprises⁷",
    "(iv) Housing",
    "(v) Education Loans",
    "(vi) Renewable Energy",
    "(vii) Social Infrastructure",
    "(viii) Export Credit",
    "(ix) Others",
    "(x) Weaker Sections including net PSLC- SF/MF",
)

V2_INDUSTRY_SOURCE_LABELS: Final = (
    "2 Industries (2.1 to 2.19)",
    "2.1 Mining & Quarrying (incl. Coal)",
    "2.2 Food Processing",
    "2.2.1 Sugar",
    "2.2.2 Edible Oils & Vanaspati",
    "2.2.3 Tea",
    "2.2.4 Others",
    "2.3 Beverage & Tobacco",
    "2.4 Textiles",
    "2.4.1 Cotton Textiles",
    "2.4.2 Jute Textiles",
    "2.4.3 Man-Made Textiles",
    "2.4.4 Other Textiles",
    "2.5 Leather & Leather Products",
    "2.6 Wood & Wood Products",
    "2.7 Paper & Paper Products",
    "2.8 Petroleum, Coal Products & Nuclear Fuels",
    "2.9 Chemicals & Chemical Products",
    "2.9.1 Fertiliser",
    "2.9.2 Drugs & Pharmaceuticals",
    "2.9.3 Petro Chemicals",
    "2.9.4 Others",
    "2.10 Rubber, Plastic & their Products",
    "2.11 Glass & Glassware",
    "2.12 Cement & Cement Products",
    "2.13 Basic Metal & Metal Product",
    "2.13.1 Iron & Steel",
    "2.13.2 Other Metal & Metal Product",
    "2.14 All Engineering",
    "2.14.1 Electronics",
    "2.14.2 Others",
    "2.15 Vehicles, Vehicle Parts & Transport Equipment",
    "2.16 Gems & Jewellery",
    "2.17 Construction",
    "2.18 Infrastructure",
    "2.18.1 Power",
    "2.18.2 Telecommunications",
    "2.18.3 Roads",
    "2.18.4 Airports",
    "2.18.5 Ports",
    "2.18.6 Railways",
    "2.18.7 Other Infrastructure",
    "2.19 Other Industries",
)


def _build_exact_mappings(
    source_table: str,
    source_labels: Sequence[str],
    v1_specs: Sequence[Any],
) -> tuple[RowMappingV2, ...]:
    if len(source_labels) != len(v1_specs):
        raise RuntimeError("v2 source-label inventory does not cover every v1 row")
    mappings: list[RowMappingV2] = []
    for source_label, spec in zip(source_labels, v1_specs, strict=True):
        observed = _normalized_taxonomy_label(source_label, spec.source_row_code)
        expected = _normalized_taxonomy_label(spec.source_label, spec.source_row_code)
        if observed != expected:
            raise RuntimeError(
                f"v2 label {source_label!r} is not exact continuity with {spec.source_label!r}"
            )
        mappings.append(
            RowMappingV2(
                source_table=source_table,
                source_row_code=spec.source_row_code,
                source_label=source_label,
                normalized_label=observed,
                parent_row_code=spec.parent_row_code,
                sector_level_1=spec.sector_level_1,
                sector_level_2=spec.sector_level_2,
                sector_level_3=spec.sector_level_3,
                is_memorandum=spec.is_memorandum,
                population_id=spec.population_id,
                base_series_id=spec.base_series_id,
                continuity_classification=EXACT_CONTINUITY,
                corresponding_v1_base_series_id=spec.base_series_id,
                footnote_references=spec.footnotes,
                emits_observations=spec.emits_observations,
            )
        )
    return tuple(mappings)


V2_MAJOR_ROW_MAPPINGS: Final = _build_exact_mappings(
    v1.MAJOR_TITLE, V2_MAJOR_SOURCE_LABELS, v1.MAJOR_ROW_SPECS
)
V2_INDUSTRY_ROW_MAPPINGS: Final = _build_exact_mappings(
    v1.INDUSTRY_TITLE, V2_INDUSTRY_SOURCE_LABELS, v1.INDUSTRY_ROW_SPECS
)
V2_ROW_MAPPINGS: Final = (*V2_MAJOR_ROW_MAPPINGS, *V2_INDUSTRY_ROW_MAPPINGS)


def validate_continuity_mappings(mappings: Sequence[RowMappingV2]) -> None:
    """Enforce the identity rule independently of label similarity."""
    for mapping in mappings:
        if mapping.continuity_classification not in CONTINUITY_CLASSES:
            raise V2ContractError(
                f"Unknown continuity classification for {mapping.source_label!r}"
            )
        reuses_v1 = mapping.corresponding_v1_base_series_id == mapping.base_series_id
        if mapping.continuity_classification == EXACT_CONTINUITY and not reuses_v1:
            raise V2ContractError(
                f"Exact-continuity row {mapping.source_label!r} does not reuse its v1 ID"
            )
        if mapping.continuity_classification != EXACT_CONTINUITY and reuses_v1:
            raise V2ContractError(
                f"Non-exact row {mapping.source_label!r} must not reuse a v1 ID"
            )


validate_continuity_mappings(V2_ROW_MAPPINGS)


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _official_rbi_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme.lower() == "https" and (
        host == "rbi.org.in" or host.endswith(".rbi.org.in")
    )


def _prepare_input(value: bytes | str, *, name: str) -> tuple[str, str]:
    _raw, text, digest = v1._prepare_input(value, name=name)
    return text, digest


def _parse_date(value: str, *, context: str) -> str:
    return v1._parse_iso_date(value, context=context)


def _semantic_title(text: str) -> tuple[str | None, str | None]:
    normalized = _normalize(text)
    match = re.fullmatch(r"(?:(\d+)\.\s*)?(.+)", normalized)
    if not match:
        return None, None
    ordinal, title = match.groups()
    for role, semantic in SEMANTIC_TITLE_BY_ROLE.items():
        if title == semantic:
            return role, ordinal
    return None, None


def _is_plain_row(row: Any, length: int) -> bool:
    return len(row.cells) == length and all(
        cell.rowspan == 1 and cell.colspan == 1 for cell in row.cells
    )


def _is_core_row(row: Any) -> bool:
    if len(row.cells) != 7 or row.cells[0].text == "":
        return False
    return row.cells[0].colspan == 1 and row.cells[0].rowspan in {1, 2} and all(
        cell.rowspan == 1 and cell.colspan == 1 for cell in row.cells[1:]
    )


def _is_parenthetical_numeric(value: str) -> bool:
    value = _normalize(value)
    return bool(re.fullmatch(r"\([+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?\)", value))


def _extract_contract_table(
    html: str,
    *,
    expected_role: str,
    source_url: str,
    source_sha256: str,
) -> _ContractTable:
    parser = v1._SemanticTableParser()
    parser.feed(html)
    title_matches: list[tuple[Any, str, str | None]] = []
    for table in parser.tables:
        for text in v1._direct_cell_texts(table):
            role, ordinal = _semantic_title(text)
            if role == expected_role:
                title_matches.append((table, _normalize(text), ordinal))
                break
    if len(title_matches) != 1:
        raise LayoutDetectionError(
            f"{expected_role}: expected one semantic title container, found {len(title_matches)}"
        )
    title_table, published_title, ordinal = title_matches[0]
    publication_cells = [
        text
        for text in v1._direct_cell_texts(title_table)
        if re.fullmatch(r"Date\s*:\s*.+", text)
    ]
    if len(publication_cells) != 1:
        raise V2ContractError(
            f"{published_title}: expected one publication date, found {publication_cells!r}"
        )
    publication_date = _parse_date(
        re.sub(r"^Date\s*:\s*", "", publication_cells[0]), context="publication"
    )
    candidates = []
    for table in v1._descendants(title_table):
        direct = {_normalize(item) for item in v1._direct_cell_texts(table)}
        compact = {item.replace(" ", "") for item in direct}
        if "(₹ Crore)" in direct and "Outstanding as on" in direct and "Growth(%)" in compact:
            candidates.append(table)
    if len(candidates) != 1:
        raise LayoutDetectionError(
            f"{published_title}: expected one semantic data table, found {len(candidates)}"
        )
    data_table = candidates[0]
    if len(data_table.rows) < 7:
        raise V2ContractError(f"{published_title}: table is too short")
    notes = [
        row
        for row in data_table.rows[5:]
        if len(row.cells) == 1
        and row.cells[0].text.startswith(("Note:", "Notes:"))
    ]
    if len(notes) != 1 or notes[0].cells[0].colspan != 7:
        raise V2ContractError(f"{published_title}: expected one seven-column notes row")
    note_index = data_table.rows.index(notes[0])
    if note_index != len(data_table.rows) - 1:
        raise V2ContractError(f"{published_title}: notes row is not terminal")
    core: list[Any] = []
    supplemental: list[Any] = []
    empty: list[Any] = []
    for row in data_table.rows[5:note_index]:
        texts = tuple(cell.text for cell in row.cells)
        if _is_core_row(row):
            core.append(row)
        elif _is_plain_row(row, 7) and texts[0] == "" and all(
            _is_parenthetical_numeric(value) for value in texts[1:]
        ):
            supplemental.append(row)
        elif len(row.cells) == 6 and all(
            cell.colspan == 1 and cell.rowspan in {1, 2} for cell in row.cells
        ) and all(_is_parenthetical_numeric(value) for value in texts):
            supplemental.append(row)
        elif all(text == "" for text in texts):
            empty.append(row)
        else:
            raise V2ContractError(
                f"{published_title}: unrecognized non-core row shape {v1._row_signature(row)!r}"
            )
    if not core:
        raise V2ContractError(f"{published_title}: no mapped economic rows")
    return _ContractTable(
        role=expected_role,
        published_title=published_title,
        table_ordinal=ordinal,
        publication_date=publication_date,
        bulletin_period=datetime.strptime(publication_date, "%Y-%m-%d").strftime("%B %Y"),
        header_rows=tuple(data_table.rows[:5]),
        data_rows=tuple(core),
        supplemental_rows=tuple(supplemental),
        empty_rows=tuple(empty),
        note=notes[0].cells[0].text,
        note_colspan=notes[0].cells[0].colspan,
        source_url=source_url,
        source_sha256=source_sha256,
    )


def _header_token(text: str) -> str:
    normalized = _normalize(text)
    compact = normalized.replace(" ", "")
    if normalized == v1.UNIT_MARKER:
        return "UNIT_INR_CRORE"
    if normalized in {"Sector", "Industry"}:
        return "ROW_LABEL"
    if normalized == "Outstanding as on":
        return "OUTSTANDING_GROUP"
    if compact == "Growth(%)":
        return "GROWTH_GROUP"
    if normalized == "":
        return "EMPTY"
    if re.fullmatch(r"[A-Z][a-z]{2}\. \d{1,2}, 20\d{2}", normalized):
        return "FULL_DATE"
    if re.fullmatch(r"20\d{2}", normalized):
        return "YEAR"
    if normalized == "Financial year so far":
        return "FINANCIAL_YEAR_SO_FAR"
    if normalized == "Y-o-Y":
        return "YEAR_ON_YEAR"
    if re.fullmatch(r"[A-Z][a-z]{2}\.? \d{1,2}", normalized):
        return "MONTH_DAY"
    if re.fullmatch(r"20\d{2}-\d{2}", normalized):
        return "FISCAL_YEAR"
    if normalized in {"1", "2", "3", "4"}:
        return f"COLUMN_{normalized}"
    if normalized in {"(1)", "(2)", "(3)", "(4)"}:
        return f"PAREN_COLUMN_{normalized[1]}"
    if normalized == "%":
        return "PERCENT"
    return f"UNKNOWN:{normalized}"


def _header_shape(table: _ContractTable) -> list[list[dict[str, Any]]]:
    return [
        [
            {"token": _header_token(cell.text), "rowspan": cell.rowspan, "colspan": cell.colspan}
            for cell in row.cells
        ]
        for row in table.header_rows
    ]


def _taxonomy_payload(
    major: _ContractTable, industry: _ContractTable, mappings: Sequence[RowMappingV2]
) -> dict[str, Any]:
    by_table = {
        v1.MAJOR_TITLE: V2_MAJOR_ROW_MAPPINGS,
        v1.INDUSTRY_TITLE: V2_INDUSTRY_ROW_MAPPINGS,
    }
    payload: dict[str, Any] = {}
    for table in (major, industry):
        expected = by_table[TITLE_BY_ROLE[table.role]]
        labels = [row.cells[0].text for row in table.data_rows]
        if len(labels) != len(expected):
            raise LayoutDetectionError(f"{table.published_title}: unexpected taxonomy row count")
        records = []
        for label, mapping in zip(labels, expected, strict=True):
            records.append(
                {
                    "code": mapping.source_row_code,
                    "normalized_label": _normalized_taxonomy_label(
                        label, mapping.source_row_code
                    ),
                    "parent": mapping.parent_row_code,
                    "memorandum": mapping.is_memorandum,
                }
            )
        payload[table.role] = records
    return payload


def _methodology_payload(major: _ContractTable, industry: _ContractTable) -> dict[str, Any]:
    combined = f"{major.note} {industry.note}"
    markers = {
        "unit": all(_header_token(t.header_rows[0].cells[0].text) == "UNIT_INR_CRORE" for t in (major, industry)),
        "section42_all_scbs": "covers all scheduled commercial banks (SCBs)" in combined,
        "sibc_select_banks": "covers select banks accounting for about 95 per cent" in combined,
        "coverage_95_percent": "95 per cent" in combined,
        "reporting_basis": (
            "LAST_REPORTING_FRIDAY"
            if "last reporting Friday of the month" in combined
            else "LAST_DAY_OF_MONTH"
            if "last day of the month" in combined
            else "UNKNOWN"
        ),
        "provisional": "Data are provisional" in combined,
    }
    return markers


def contract_signatures(
    major: _ContractTable, industry: _ContractTable
) -> ContractSignatures:
    structural = {
        "titles": [SEMANTIC_TITLE_BY_ROLE[table.role] for table in (major, industry)],
        "headers": {table.role: _header_shape(table) for table in (major, industry)},
        "core_row_shape": {table.role: [7, len(table.data_rows)] for table in (major, industry)},
        "note": {"terminal": True, "colspan": 7},
        "recognized_non_core_shapes": ["PARENTHETICAL_SIX_VALUES", "EMPTY_SPACER"],
    }
    taxonomy = _taxonomy_payload(major, industry, V2_ROW_MAPPINGS)
    methodology = _methodology_payload(major, industry)
    return ContractSignatures(
        _sha256_json(structural), _sha256_json(taxonomy), _sha256_json(methodology)
    )


def _expected_payloads(layout: str) -> ContractSignatures:
    top_major = [
        ("ROW_LABEL", 4, 1),
        ("OUTSTANDING_GROUP", 1, 4 if layout == LAYOUT_ID else 3),
        ("GROWTH_GROUP", 1, 2),
    ]
    if layout == v1.LAYOUT_ID:
        top_major.append(("EMPTY", 1, 1))
    top_industry = [
        ("ROW_LABEL", 4, 1),
        ("OUTSTANDING_GROUP", 1, 4),
        ("GROWTH_GROUP", 1, 2),
    ]
    def cells(items: Sequence[tuple[str, int, int]]) -> list[dict[str, Any]]:
        return [{"token": a, "rowspan": b, "colspan": c} for a, b, c in items]
    column_prefix = "COLUMN" if layout == LAYOUT_ID else "PAREN_COLUMN"
    rest = [
        cells([("FULL_DATE", 2, 1), ("YEAR", 1, 1), ("YEAR", 1, 2), ("FINANCIAL_YEAR_SO_FAR", 1, 1), ("YEAR_ON_YEAR", 1, 1)]),
        cells([("MONTH_DAY", 1, 1), ("MONTH_DAY", 1, 1), ("MONTH_DAY", 1, 1), ("FISCAL_YEAR", 1, 1), ("YEAR", 1, 1)]),
        cells([(f"{column_prefix}_1", 1, 1), (f"{column_prefix}_2", 1, 1), (f"{column_prefix}_3", 1, 1), (f"{column_prefix}_4", 1, 1), ("PERCENT", 1, 1), ("PERCENT", 1, 1)]),
    ]
    structural = {
        "titles": [MAJOR_SEMANTIC_TITLE, INDUSTRY_SEMANTIC_TITLE],
        "headers": {
            "major": [cells([("UNIT_INR_CRORE", 1, 7)]), cells(top_major), *rest],
            "industry": [cells([("UNIT_INR_CRORE", 1, 7)]), cells(top_industry), *rest],
        },
        "core_row_shape": {"major": [7, 44], "industry": [7, 43]},
        "note": {"terminal": True, "colspan": 7},
        "recognized_non_core_shapes": ["PARENTHETICAL_SIX_VALUES", "EMPTY_SPACER"],
    }
    taxonomy = {
        role: [
            {
                "code": m.source_row_code,
                "normalized_label": m.normalized_label,
                "parent": m.parent_row_code,
                "memorandum": m.is_memorandum,
            }
            for m in mappings
        ]
        for role, mappings in (
            ("major", V2_MAJOR_ROW_MAPPINGS),
            ("industry", V2_INDUSTRY_ROW_MAPPINGS),
        )
    }
    methodology = {
        "unit": True,
        "section42_all_scbs": True,
        "sibc_select_banks": True,
        "coverage_95_percent": True,
        "reporting_basis": "LAST_REPORTING_FRIDAY" if layout == LAYOUT_ID else "LAST_DAY_OF_MONTH",
        "provisional": True,
    }
    return ContractSignatures(
        _sha256_json(structural), _sha256_json(taxonomy), _sha256_json(methodology)
    )


EXPECTED_V2_SIGNATURES: Final = _expected_payloads(LAYOUT_ID)
EXPECTED_V1_SIGNATURES: Final = _expected_payloads(v1.LAYOUT_ID)


def _detect_from_signatures(
    observed: ContractSignatures,
    candidates: Sequence[tuple[str, ContractSignatures]],
) -> str:
    matches = [layout for layout, expected in candidates if observed == expected]
    if not matches:
        raise LayoutDetectionError(
            "No supported sectoral-credit layout matches all structural, taxonomy, and methodology signatures"
        )
    if len(matches) > 1:
        raise AmbiguousLayoutDetectionError(f"Multiple layouts match: {matches!r}")
    return matches[0]


def detect_sectoral_credit_layout(
    major_sectors_html: bytes | str,
    industries_html: bytes | str,
    *,
    major_sectors_url: str = "https://rbi.org.in/major",
    industries_url: str = "https://rbi.org.in/industry",
) -> tuple[str, ContractSignatures]:
    major_text, major_hash = _prepare_input(major_sectors_html, name="major_sectors_html")
    industry_text, industry_hash = _prepare_input(industries_html, name="industries_html")
    major = _extract_contract_table(
        major_text, expected_role="major", source_url=major_sectors_url, source_sha256=major_hash
    )
    industry = _extract_contract_table(
        industry_text,
        expected_role="industry",
        source_url=industries_url,
        source_sha256=industry_hash,
    )
    observed = contract_signatures(major, industry)
    return _detect_from_signatures(
        observed,
        ((v1.LAYOUT_ID, EXPECTED_V1_SIGNATURES), (LAYOUT_ID, EXPECTED_V2_SIGNATURES)),
    ), observed


def _parse_header_dates(table: _ContractTable) -> _HeaderDates:
    if len(table.header_rows) != 5 or _header_shape(table)[0] != [
        {"token": "UNIT_INR_CRORE", "rowspan": 1, "colspan": 7}
    ]:
        raise V2ContractError(f"{table.published_title}: invalid header depth or unit")
    second = table.header_rows[2].cells
    third = table.header_rows[3].cells
    if len(second) != 5 or len(third) != 5:
        raise V2ContractError(f"{table.published_title}: invalid date header shape")
    full_date = _parse_date(second[0].text, context="financial-year base")
    year_cells = second[1:3]
    years = [
        cell.text
        for cell in year_cells
        for _ in range(cell.colspan)
    ]
    if len(years) != 3 or not all(re.fullmatch(r"20\d{2}", year) for year in years):
        raise V2ContractError(f"{table.published_title}: invalid year grouping")
    outstanding_dates = [
        v1._combine_month_day_year(cell.text, year, context=f"outstanding column {index}")
        for index, (cell, year) in enumerate(zip(third[:3], years, strict=True), start=2)
    ]
    prior, prior_period, current = outstanding_dates
    return _HeaderDates(full_date, prior, prior_period, current, third[3].text)


def _validate_release_contract(
    major: _ContractTable, industry: _ContractTable
) -> tuple[_HeaderDates, str | None, int | None]:
    if major.published_title != v1.MAJOR_TITLE or industry.published_title != v1.INDUSTRY_TITLE:
        raise V2ContractError("Published table titles or ordinals do not match Tables 15 and 16")
    if major.table_ordinal != "15" or industry.table_ordinal != "16":
        raise V2ContractError("Published table ordinals do not match 15/16")
    if major.publication_date != industry.publication_date:
        raise V2ContractError("The table pair has different publication dates")
    if major.bulletin_period != industry.bulletin_period:
        raise V2ContractError("The table pair has different Bulletin periods")
    if major.bulletin_period not in SUPPORTED_BULLETIN_PERIODS:
        raise V2ContractError(
            f"Unsupported v2 Bulletin period {major.bulletin_period!r}; parser scope is 2025H2"
        )
    major_dates = _parse_header_dates(major)
    industry_dates = _parse_header_dates(industry)
    if major_dates != industry_dates:
        raise V2ContractError("The table pair has different date/fiscal headers")
    combined = f"{major.note} {industry.note}"
    required = (
        "Data are provisional",
        "Section-42 return",
        "covers all scheduled commercial banks (SCBs)",
        "sector-wise and industry-wise bank credit (SIBC) return",
        "covers select banks accounting for about 95 per cent",
        "last reporting Friday of the month",
    )
    missing = [marker for marker in required if marker not in combined]
    if missing:
        raise V2ContractError(f"Required methodology markers missing: {missing!r}")
    has_parenthetical = bool(major.supplemental_rows or industry.supplemental_rows)
    marker = "Figures in parentheses exclude the impact of the merger"
    if has_parenthetical != (marker in combined):
        raise V2ContractError("Supplemental parenthetical rows and explanatory note disagree")
    override = None
    override_match = re.search(
        r"Bank credit, Food credit and Non-food credit given for the period "
        r"([A-Z][a-z]+ \d{1,2}, 20\d{2}) pertains to "
        r"([A-Z][a-z]+ \d{1,2}, 20\d{2})",
        major.note,
    )
    if override_match:
        visible = datetime.strptime(override_match.group(1), "%B %d, %Y").date().isoformat()
        override = datetime.strptime(override_match.group(2), "%B %d, %Y").date().isoformat()
        if visible != major_dates.prior_year_reference_date:
            raise V2ContractError("Section-42 override does not refer to the visible prior date")
    reporting_count_match = re.search(r"(?:covers|covering)\s+(\d+)\s+banks", combined)
    reporting_count = int(reporting_count_match.group(1)) if reporting_count_match else None
    return major_dates, override, reporting_count


def _map_rows(
    source: _ContractTable, mappings: Sequence[RowMappingV2]
) -> tuple[tuple[_ParsedMappedRow, ...], int]:
    labels = [row.cells[0].text for row in source.data_rows]
    expected = [mapping.source_label for mapping in mappings]
    if len(labels) != len(set(labels)):
        raise V2ContractError(f"{source.published_title}: duplicate core source label")
    if labels != expected:
        unknown = [label for label in labels if label not in set(expected)]
        missing = [label for label in expected if label not in set(labels)]
        raise v1.UnmappedSeriesError(
            f"{source.published_title}: unknown rows={unknown!r}; missing rows={missing!r}; order exact={not unknown and not missing}"
        )
    parsed: list[_ParsedMappedRow] = []
    duplicate_columns = 0
    dates = _parse_header_dates(source)
    for raw_row, mapping in zip(source.data_rows, mappings, strict=True):
        values = tuple(
            v1._parse_numeric(
                cell.text,
                context=f"{source.published_title} row {mapping.source_label!r} column {index}",
            )
            for index, cell in enumerate(raw_row.cells[1:], start=1)
        )
        if not mapping.emits_observations:
            if any(value is not None for value in values):
                raise v1.DataValidationError(
                    f"Structural row {mapping.source_label!r} unexpectedly contains values"
                )
        elif dates.financial_year_base_date == dates.prior_period_reference_date:
            if values[0] != values[2]:
                raise v1.DataValidationError(
                    f"{mapping.source_label!r}: repeated-date source columns (1) and (3) disagree"
                )
            duplicate_columns += 1
        parsed.append(_ParsedMappedRow(mapping, values))
    return tuple(parsed), duplicate_columns


def _reconcile_growth(rows: Iterable[_ParsedMappedRow]) -> tuple[int, int]:
    checks = 0
    skipped = 0
    for row in rows:
        if not row.mapping.emits_observations:
            continue
        for name, comparison, current, reported in (
            ("financial-year", row.values[0], row.values[3], row.values[4]),
            ("year-on-year", row.values[1], row.values[3], row.values[5]),
        ):
            if comparison is None or current is None or reported is None or comparison == 0:
                skipped += 1
                continue
            checks += 1
            implied = ((current / comparison) - Decimal(1)) * Decimal(100)
            if abs(implied - reported) > v1.ROUNDING_TOLERANCE:
                raise v1.DataValidationError(
                    f"Growth reconciliation failed for {row.mapping.source_label!r} {name}: "
                    f"reported={reported}, implied={implied}, tolerance={v1.ROUNDING_TOLERANCE}"
                )
    return checks, skipped


def _release_observation(
    *,
    source: _ContractTable,
    row: _ParsedMappedRow,
    measure: str,
    observation_date: str,
    comparison_date: str | None,
    publication_date: str,
    bulletin_period: str,
    value: Decimal | None,
    unit: str,
    column_role: str,
    layout_id: str = LAYOUT_ID,
    parser_version: str = PARSER_VERSION,
) -> dict[str, Any]:
    mapping = row.mapping
    return {
        "dataset_id": v1.DATASET_ID,
        "series_id": v1._series_id(mapping, measure),
        "source_table": source.published_title,
        "source_row_code": mapping.source_row_code,
        "source_label": mapping.source_label,
        "measure": measure,
        "observation_date": observation_date,
        "comparison_date": comparison_date,
        "publication_date": publication_date,
        "bulletin_period": bulletin_period,
        "value": value,
        "unit": unit,
        "population_id": mapping.population_id,
        "column_role": column_role,
        "layout_id": layout_id,
        "parser_version": parser_version,
        "source_url": source.source_url,
        "source_sha256": source.source_sha256,
        "is_provisional": True,
        "footnote_references": json.dumps(
            list(mapping.footnote_references), separators=(",", ":")
        ),
    }


def _rows_to_observations(
    source: _ContractTable,
    rows: Iterable[_ParsedMappedRow],
    dates: _HeaderDates,
    *,
    section42_override: str | None,
    skip_source_row_code: str | None = None,
    layout_id: str = LAYOUT_ID,
    parser_version: str = PARSER_VERSION,
) -> tuple[list[dict[str, Any]], int]:
    output: list[dict[str, Any]] = []
    emitted = 0
    for row in rows:
        mapping = row.mapping
        if not mapping.emits_observations or mapping.source_row_code == skip_source_row_code:
            continue
        emitted += 1
        prior_date = dates.prior_year_reference_date
        if source.role == "major" and mapping.population_id == v1.POPULATION_ALL:
            prior_date = section42_override or prior_date
        common = {
            "source": source,
            "row": row,
            "publication_date": source.publication_date,
            "bulletin_period": source.bulletin_period,
            "layout_id": layout_id,
            "parser_version": parser_version,
        }
        release_rows = [
                _release_observation(
                    **common, measure=v1.MEASURE_OUTSTANDING,
                    observation_date=prior_date, comparison_date=None,
                    value=row.values[1], unit=v1.UNIT_INR_CRORE,
                    column_role=PRIOR_YEAR_REFERENCE,
                ),
                _release_observation(
                    **common, measure=v1.MEASURE_OUTSTANDING,
                    observation_date=dates.financial_year_base_date, comparison_date=None,
                    value=row.values[0], unit=v1.UNIT_INR_CRORE,
                    column_role=FINANCIAL_YEAR_BASE,
                ),
                _release_observation(
                    **common, measure=v1.MEASURE_OUTSTANDING,
                    observation_date=dates.current_observation_date, comparison_date=None,
                    value=row.values[3], unit=v1.UNIT_INR_CRORE,
                    column_role=CURRENT_OBSERVATION,
                ),
                _release_observation(
                    **common, measure=v1.MEASURE_FY_GROWTH,
                    observation_date=dates.current_observation_date,
                    comparison_date=dates.financial_year_base_date,
                    value=row.values[4], unit=v1.UNIT_PERCENT,
                    column_role=REPORTED_FINANCIAL_YEAR_GROWTH,
                ),
                _release_observation(
                    **common, measure=v1.MEASURE_YOY_GROWTH,
                    observation_date=dates.current_observation_date,
                    comparison_date=prior_date, value=row.values[5],
                    unit=v1.UNIT_PERCENT, column_role=REPORTED_YOY_GROWTH,
                ),
            ]
        if dates.prior_period_reference_date != dates.financial_year_base_date:
            release_rows.insert(
                2,
                _release_observation(
                    **common,
                    measure=v1.MEASURE_OUTSTANDING,
                    observation_date=dates.prior_period_reference_date,
                    comparison_date=None,
                    value=row.values[2],
                    unit=v1.UNIT_INR_CRORE,
                    column_role=PRIOR_PERIOD_REFERENCE,
                ),
            )
        output.extend(release_rows)
    return output, emitted


def release_observations_to_csv_bytes(observations: pd.DataFrame) -> bytes:
    if tuple(observations.columns) != RELEASE_OBSERVATION_COLUMNS:
        raise v1.DataValidationError("Release observations do not match the 20-field schema")
    return v1._observations_csv_bytes(
        observations, RELEASE_OBSERVATION_COLUMNS, sort_rows=False, missing_value=""
    )


def release_semantic_sha256(observations: pd.DataFrame) -> str:
    return hashlib.sha256(
        v1._observations_csv_bytes(
            observations,
            RELEASE_SEMANTIC_COLUMNS,
            sort_rows=True,
            missing_value=v1.SEMANTIC_MISSING_VALUE,
        )
    ).hexdigest()


def release_provenance_sha256(observations: pd.DataFrame) -> str:
    return hashlib.sha256(release_observations_to_csv_bytes(observations)).hexdigest()


def _count_values(observations: pd.DataFrame, column: str) -> tuple[tuple[str, int], ...]:
    counts = observations[column].value_counts(dropna=False).to_dict()
    return tuple(sorted((str(key), int(value)) for key, value in counts.items()))


def parse_sectoral_credit_bulletin_v2_release(
    major_sectors_html: bytes | str,
    industries_html: bytes | str,
    *,
    major_sectors_url: str,
    industries_url: str,
) -> ParsedSectoralCreditRelease:
    """Parse one cached 2025H2 table pair into publication-vintage observations."""
    for name, url in (("major", major_sectors_url), ("industry", industries_url)):
        if not _official_rbi_url(url):
            raise V2ContractError(f"{name} source URL is not official RBI HTTPS: {url!r}")
    major_text, major_hash = _prepare_input(major_sectors_html, name="major_sectors_html")
    industry_text, industry_hash = _prepare_input(industries_html, name="industries_html")
    major = _extract_contract_table(
        major_text, expected_role="major", source_url=major_sectors_url, source_sha256=major_hash
    )
    industry = _extract_contract_table(
        industry_text,
        expected_role="industry",
        source_url=industries_url,
        source_sha256=industry_hash,
    )
    signatures = contract_signatures(major, industry)
    detected = _detect_from_signatures(signatures, ((LAYOUT_ID, EXPECTED_V2_SIGNATURES),))
    if detected != LAYOUT_ID:  # pragma: no cover - defensive invariant
        raise LayoutDetectionError("v2 parser received a non-v2 layout")
    dates, override, reporting_count = _validate_release_contract(major, industry)
    major_rows, major_duplicate_columns = _map_rows(major, V2_MAJOR_ROW_MAPPINGS)
    industry_rows, industry_duplicate_columns = _map_rows(
        industry, V2_INDUSTRY_ROW_MAPPINGS
    )
    major_total = next(row for row in major_rows if row.mapping.source_row_code == "2")
    industry_total = next(row for row in industry_rows if row.mapping.source_row_code == "2")
    if major_total.mapping.base_series_id != industry_total.mapping.base_series_id or major_total.values != industry_total.values:
        raise v1.DataValidationError("Cross-table duplicate Industry row disagrees")
    growth_checks, growth_skipped = _reconcile_growth(
        (
            *major_rows,
            *(row for row in industry_rows if row.mapping.source_row_code != "2"),
        )
    )
    major_obs, major_emitted = _rows_to_observations(
        major, major_rows, dates, section42_override=override
    )
    industry_obs, industry_emitted = _rows_to_observations(
        industry,
        industry_rows,
        dates,
        section42_override=override,
        skip_source_row_code="2",
    )
    observations = pd.DataFrame(
        [*major_obs, *industry_obs], columns=RELEASE_OBSERVATION_COLUMNS
    )
    duplicate_mask = observations.duplicated(list(VINTAGE_KEY_COLUMNS), keep=False)
    duplicate_count = int(duplicate_mask.sum())
    if duplicate_count:
        raise v1.DataValidationError("Duplicate publication-vintage keys in release output")
    metadata = ReleaseMetadata(
        dataset_id=v1.DATASET_ID,
        layout_id=LAYOUT_ID,
        parser_version=PARSER_VERSION,
        publication_date=major.publication_date,
        bulletin_period=major.bulletin_period,
        current_observation_date=dates.current_observation_date,
        prior_year_reference_date=dates.prior_year_reference_date,
        financial_year_base_date=dates.financial_year_base_date,
        section42_prior_year_override_date=override,
        source_hashes=(("major", major.source_sha256), ("industry", industry.source_sha256)),
        structural_signature_sha256=signatures.structural_signature_sha256,
        taxonomy_signature_sha256=signatures.taxonomy_signature_sha256,
        methodology_signature_sha256=signatures.methodology_signature_sha256,
        mapped_row_counts=(("major", len(major_rows)), ("industry", len(industry_rows))),
        emitted_row_counts=(("major", major_emitted), ("industry", industry_emitted)),
        supplemental_non_emitting_row_counts=(("major", len(major.supplemental_rows)), ("industry", len(industry.supplemental_rows))),
        ignored_empty_row_counts=(("major", len(major.empty_rows)), ("industry", len(industry.empty_rows))),
        duplicate_source_column_count=(
            major_duplicate_columns
            + industry_duplicate_columns
            - (1 if industry_duplicate_columns else 0)
        ),
        duplicate_source_row_count=1,
        growth_reconciliation_checks=growth_checks,
        growth_reconciliation_skipped=growth_skipped,
        growth_reconciliation_failures=0,
        canonical_release_key_duplicate_count=duplicate_count,
        observation_counts_by_measure=_count_values(observations, "measure"),
        observation_counts_by_population=_count_values(observations, "population_id"),
        observation_counts_by_column_role=_count_values(observations, "column_role"),
        unique_series_count=int(observations["series_id"].nunique()),
        coverage_percentage="95",
        reporting_bank_count=reporting_count,
        release_semantic_sha256=release_semantic_sha256(observations),
        provenance_bound_output_sha256=release_provenance_sha256(observations),
    )
    return ParsedSectoralCreditRelease(
        observations=observations,
        metadata=metadata,
        notes=(
            v1.SourceNote(major.published_title, major.note),
            v1.SourceNote(industry.published_title, industry.note),
        ),
    )


def parse_sectoral_credit_bulletin_detected(
    major_sectors_html: bytes | str,
    industries_html: bytes | str,
    *,
    major_sectors_url: str,
    industries_url: str,
) -> v1.ParsedSectoralCredit | ParsedSectoralCreditRelease:
    """Dispatch only after a positive, three-signature layout match."""
    layout, _signatures = detect_sectoral_credit_layout(
        major_sectors_html,
        industries_html,
        major_sectors_url=major_sectors_url,
        industries_url=industries_url,
    )
    if layout == v1.LAYOUT_ID:
        return v1.parse_sectoral_credit_bulletin(
            major_sectors_html,
            industries_html,
            major_sectors_url=major_sectors_url,
            industries_url=industries_url,
        )
    return parse_sectoral_credit_bulletin_v2_release(
        major_sectors_html,
        industries_html,
        major_sectors_url=major_sectors_url,
        industries_url=industries_url,
    )


__all__ = [
    "AmbiguousLayoutDetectionError",
    "COLUMN_ROLES",
    "ContractSignatures",
    "LAYOUT_ID",
    "LayoutDetectionError",
    "ParsedSectoralCreditRelease",
    "RELEASE_OBSERVATION_COLUMNS",
    "ReleaseMetadata",
    "RowMappingV2",
    "SUPPORTED_BULLETIN_PERIODS",
    "V2ContractError",
    "V2_ROW_MAPPINGS",
    "detect_sectoral_credit_layout",
    "parse_sectoral_credit_bulletin_detected",
    "parse_sectoral_credit_bulletin_v2_release",
    "release_observations_to_csv_bytes",
    "validate_continuity_mappings",
]
