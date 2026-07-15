"""Offline parser contracts for the January-May 2026 RBI transition.

The module extends positive layout inspection across the published-vintage
staging boundary without changing the public snapshot connector.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any, Final, Iterable, Sequence

import pandas as pd

from indiamacro.rbi import sectoral_credit_bulletin as v1
from indiamacro.rbi import sectoral_credit_bulletin_v2 as v2


LAYOUT_JANUARY: Final = "RBI_BULLETIN_SECTORAL_CREDIT_V3_2026_01_LAST_FRIDAY"
LAYOUT_FEBRUARY_APRIL: Final = (
    "RBI_BULLETIN_SECTORAL_CREDIT_V4_2026_02_04_MONTH_END"
)
LAYOUT_MAY: Final = "RBI_BULLETIN_SECTORAL_CREDIT_V5_2026_05_FY_CLOSE"
PARSER_VERSION: Final = "3.0.0"
SUPPORTED_PERIODS: Final = (
    "January 2026",
    "February 2026",
    "March 2026",
    "April 2026",
    "May 2026",
)

FINANCIAL_YEAR_AND_PRIOR_YEAR_REFERENCE: Final = (
    "FINANCIAL_YEAR_AND_PRIOR_YEAR_REFERENCE"
)

FULLY_COMPARABLE: Final = "FULLY_COMPARABLE"
COMPARABLE_WITH_DATE_BASIS_CHANGE: Final = "COMPARABLE_WITH_DATE_BASIS_CHANGE"
METHODOLOGY_CHANGED: Final = "METHODOLOGY_CHANGED"
NOT_COMPARABLE: Final = "NOT_COMPARABLE"
REQUIRES_REVIEW: Final = "REQUIRES_REVIEW"
COMPARABILITY_CLASSES: Final = (
    FULLY_COMPARABLE,
    COMPARABLE_WITH_DATE_BASIS_CHANGE,
    METHODOLOGY_CHANGED,
    NOT_COMPARABLE,
    REQUIRES_REVIEW,
)


class TransitionContractError(v2.V2ContractError):
    """A cached transition release violates its explicit contract."""


@dataclass(frozen=True)
class DetectedLayout:
    layout_id: str
    signatures: v2.ContractSignatures
    bulletin_period: str


@dataclass(frozen=True)
class BoundaryClassification:
    from_layout_id: str
    to_layout_id: str
    taxonomy_continuity: str
    comparability: str
    methodology_change: str


@dataclass(frozen=True)
class _MayTable:
    role: str
    published_title: str
    table_ordinal: str
    publication_date: str
    bulletin_period: str
    header_rows: tuple[Any, ...]
    data_rows: tuple[Any, ...]
    note: str
    source_url: str
    source_sha256: str


@dataclass(frozen=True)
class _MayDates:
    combined_base_date: str
    prior_period_reference_date: str
    current_observation_date: str
    fiscal_year: str


@dataclass(frozen=True)
class _MayRow:
    mapping: v2.RowMappingV2
    values: tuple[Decimal | None, ...]


_PLAIN_FOOTNOTE_REPLACEMENTS: Final = {
    "3.7.1. Wholesale Trade¹": "3.7.1. Wholesale Trade 1",
    "3.9 Non-Banking Financial Companies (NBFCs)² of which,": (
        "3.9 Non-Banking Financial Companies (NBFCs) 2 of which,"
    ),
    "3.10 Other Services³": "3.10 Other Services 3",
}


def _mapping_profile(period: str, role: str) -> tuple[v2.RowMappingV2, ...]:
    base = v2.V2_MAJOR_ROW_MAPPINGS if role == "major" else v2.V2_INDUSTRY_ROW_MAPPINGS
    labels = [getattr(spec, "source_label") for spec in (
        v1.MAJOR_ROW_SPECS if role == "major" else v1.INDUSTRY_ROW_SPECS
    )]
    if role == "major" and period in {
        "January 2026",
        "February 2026",
        "April 2026",
        "May 2026",
    }:
        labels = [_PLAIN_FOOTNOTE_REPLACEMENTS.get(label, label) for label in labels]
    if role == "industry" and period in {"January 2026", "February 2026"}:
        labels[0] = "2 Industries (2.1 to 2.19)"
    return tuple(
        replace(mapping, source_label=label)
        for mapping, label in zip(base, labels, strict=True)
    )


MAPPINGS_BY_PERIOD: Final = {
    period: {
        role: _mapping_profile(period, role)
        for role in ("major", "industry")
    }
    for period in SUPPORTED_PERIODS
}


BOUNDARY_CLASSIFICATIONS: Final = (
    BoundaryClassification(
        v2.LAYOUT_ID,
        LAYOUT_JANUARY,
        v2.EXACT_CONTINUITY,
        FULLY_COMPARABLE,
        "Same last-reporting-Friday regime; exact labels use a different footnote presentation.",
    ),
    BoundaryClassification(
        LAYOUT_JANUARY,
        LAYOUT_FEBRUARY_APRIL,
        v2.EXACT_CONTINUITY,
        COMPARABLE_WITH_DATE_BASIS_CHANGE,
        "Current observations change to calendar month-end while YoY bases retain the old reporting-fortnight definition.",
    ),
    BoundaryClassification(
        LAYOUT_FEBRUARY_APRIL,
        LAYOUT_MAY,
        v2.EXACT_CONTINUITY,
        FULLY_COMPARABLE,
        "Methodology is unchanged; the FY-close source combines financial-year and prior-year base roles.",
    ),
    BoundaryClassification(
        LAYOUT_MAY,
        v1.LAYOUT_ID,
        v2.EXACT_CONTINUITY,
        FULLY_COMPARABLE,
        "Methodology is unchanged; June repeats the financial-year base in two source columns and v1 collapses it.",
    ),
)


def _sha_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _methodology_detail(note: str) -> dict[str, Any]:
    month_end = "definition of last reporting fortnight has been changed to the last day of the month" in note
    return {
        "unit": "INR_CRORE",
        "section42_population": "ALL_SCBS",
        "sibc_population": "SELECT_SCBS_ABOUT_95_PERCENT",
        "provisional": "Data are provisional" in note,
        "current_date_basis": "CALENDAR_MONTH_END" if month_end else "LAST_REPORTING_FRIDAY",
        "yoy_comparison_basis": (
            "CURRENT_EOM_VS_PRIOR_OLD_REPORTING_FORTNIGHT"
            if month_end
            else "CORRESPONDING_LAST_REPORTING_FRIDAY"
        ),
    }


def _layout_for_period(period: str) -> str:
    if period == "January 2026":
        return LAYOUT_JANUARY
    if period in {"February 2026", "March 2026", "April 2026"}:
        return LAYOUT_FEBRUARY_APRIL
    if period == "May 2026":
        return LAYOUT_MAY
    raise TransitionContractError(f"Unsupported transition period {period!r}")


def _validate_exact_labels(
    major: Any, industry: Any, period: str
) -> None:
    for table in (major, industry):
        mappings = MAPPINGS_BY_PERIOD[period][table.role]
        labels = [row.cells[0].text for row in table.data_rows]
        expected = [mapping.source_label for mapping in mappings]
        if labels != expected:
            unknown = [label for label in labels if label not in set(expected)]
            missing = [label for label in expected if label not in set(labels)]
            raise v1.UnmappedSeriesError(
                f"{table.published_title}: unknown={unknown!r}; missing={missing!r}; exact order required"
            )


def _validate_four_outstanding_header(table: Any, period: str) -> None:
    if len(table.header_rows) != 5:
        raise TransitionContractError("Four-outstanding header must contain five rows")
    expected_label = "Sector" if table.role == "major" else "Industry"
    if v1._row_signature(table.header_rows[0]) != ((v1.UNIT_MARKER, 1, 7),):
        raise TransitionContractError("Four-outstanding unit header is invalid")
    growth_header = "Growth (%)" if period in {"March 2026", "April 2026"} else "Growth(%)"
    if v1._row_signature(table.header_rows[1]) != (
        (expected_label, 4, 1),
        ("Outstanding as on", 1, 4),
        (growth_header, 1, 2),
    ):
        raise TransitionContractError("Four-outstanding top header is invalid")
    second = v1._row_signature(table.header_rows[2])
    expected_spans = (2, 1) if period == "March 2026" else (1, 2)
    if (
        len(second) != 5
        or second[0][1:] != (2, 1)
        or second[1][1:] != (1, expected_spans[0])
        or second[2][1:] != (1, expected_spans[1])
        or second[3] != ("Financial year so far", 1, 1)
        or second[4] != ("Y-o-Y", 1, 1)
    ):
        raise TransitionContractError("Four-outstanding year grouping is invalid")
    if len(table.header_rows[3].cells) != 5:
        raise TransitionContractError("Four-outstanding date header is invalid")
    if v1._row_signature(table.header_rows[4]) != tuple(
        (value, 1, 1) for value in ("1", "2", "3", "4", "%", "%")
    ):
        raise TransitionContractError("Four-outstanding column roles are invalid")
    v2._parse_header_dates(table)


def _validate_may_header(table: _MayTable) -> None:
    expected_label = "Sector" if table.role == "major" else "Industry"
    if v1._row_signature(table.header_rows[1]) != (
        (expected_label, 4, 1),
        ("Outstanding as on", 1, 3),
        ("Growth (%)", 1, 2),
    ):
        raise TransitionContractError("May top header is invalid")
    second = v1._row_signature(table.header_rows[2])
    if (
        len(second) != 4
        or second[0][1:] != (2, 1)
        or second[1][1:] != (1, 2)
        or second[2] != ("Financial year so far", 1, 1)
        or second[3] != ("Y-o-Y", 1, 1)
    ):
        raise TransitionContractError("May year header is invalid")
    if len(table.header_rows[3].cells) != 4 or v1._row_signature(table.header_rows[4]) != tuple(
        (value, 1, 1) for value in ("1", "2", "3", "4", "5")
    ):
        raise TransitionContractError("May date or column-role header is invalid")
    _may_dates(table)


def _validate_common_pair(major: Any, industry: Any, period: str) -> None:
    if major.published_title != v1.MAJOR_TITLE or industry.published_title != v1.INDUSTRY_TITLE:
        raise TransitionContractError("Transition table titles must be exact Tables 15 and 16")
    if major.table_ordinal != "15" or industry.table_ordinal != "16":
        raise TransitionContractError("Transition table ordinals must be 15 and 16")
    if major.publication_date != industry.publication_date or major.bulletin_period != period or industry.bulletin_period != period:
        raise TransitionContractError("Transition table pair publication identity disagrees")
    for table in (major, industry):
        if period == "May 2026":
            _validate_may_header(table)
        else:
            _validate_four_outstanding_header(table, period)
    combined = f"{major.note} {industry.note}"
    required = (
        "Data are provisional",
        "Section-42 return",
        "covers all scheduled commercial banks (SCBs)",
        "sector-wise and industry-wise bank credit (SIBC) return",
        "covers select banks accounting for about 95 per cent",
    )
    missing = [marker for marker in required if marker not in combined]
    if missing:
        raise TransitionContractError(f"Required population markers missing: {missing!r}")
    if period == "January 2026":
        if "last reporting Friday of the month" not in combined:
            raise TransitionContractError("January must use the last-reporting-Friday regime")
    elif "definition of last reporting fortnight has been changed to the last day of the month" not in combined:
        raise TransitionContractError("Month-end transition methodology marker is missing")
    _validate_exact_labels(major, industry, period)


def _extract_section42_override(note: str, visible: str, *, column: str) -> str | None:
    patterns = (
        r"given for the period ([A-Z][a-z]+ \d{1,2}, 20\d{2}) pertains to ([A-Z][a-z]+ \d{1,2}, 20\d{2})",
        rf"Reference date for Section-42 data \(rows I, II & III\) in Column \({column}\) is ([A-Z][a-z]+ \d{{1,2}}, 20\d{{2}})",
    )
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, note)
        if not match:
            continue
        if index == 0:
            stated = datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat()
            if stated != visible:
                raise TransitionContractError("Section-42 override visible date disagrees")
            value = match.group(2)
        else:
            value = match.group(1)
        return datetime.strptime(value, "%B %d, %Y").date().isoformat()
    return None


def _transition_signatures(major: Any, industry: Any, period: str) -> v2.ContractSignatures:
    structural = {
        "titles": [major.published_title, industry.published_title],
        "headers": {table.role: v2._header_shape(table) for table in (major, industry)},
        "column_counts": {table.role: len(table.header_rows[0].cells) and table.header_rows[0].cells[0].colspan for table in (major, industry)},
        "exact_label_presentation": {
            table.role: [row.cells[0].text for row in table.data_rows]
            for table in (major, industry)
        },
        "note_colspan": {table.role: table.note_colspan for table in (major, industry)},
    }
    taxonomy = {
        role: [
            {
                "code": mapping.source_row_code,
                "normalized_label": mapping.normalized_label,
                "parent": mapping.parent_row_code,
                "memorandum": mapping.is_memorandum,
            }
            for mapping in MAPPINGS_BY_PERIOD[period][role]
        ]
        for role in ("major", "industry")
    }
    methodology = _methodology_detail(f"{major.note} {industry.note}")
    return v2.ContractSignatures(
        _sha_json(structural), _sha_json(taxonomy), _sha_json(methodology)
    )


def _extract_may(
    html: str, *, role: str, source_url: str, source_sha256: str
) -> _MayTable:
    parser = v1._SemanticTableParser()
    parser.feed(html)
    matches = []
    semantic = v2.SEMANTIC_TITLE_BY_ROLE[role]
    for table in parser.tables:
        for text in v1._direct_cell_texts(table):
            match = re.fullmatch(r"(\d+)\.\s*(.+)", v2._normalize(text))
            if match and match.group(2) == semantic:
                matches.append((table, v2._normalize(text), match.group(1)))
                break
    if len(matches) != 1:
        raise v2.LayoutDetectionError(f"May {role}: expected one title container")
    title_table, published_title, ordinal = matches[0]
    dates = [text for text in v1._direct_cell_texts(title_table) if re.fullmatch(r"Date\s*:\s*.+", text)]
    if len(dates) != 1:
        raise TransitionContractError("May table must contain one publication date")
    publication_date = v1._parse_iso_date(re.sub(r"^Date\s*:\s*", "", dates[0]), context="publication")
    candidates = []
    for table in v1._descendants(title_table):
        direct = {v2._normalize(text) for text in v1._direct_cell_texts(table)}
        if v1.UNIT_MARKER in direct and "Outstanding as on" in direct and any(text.replace(" ", "") == "Growth(%)" for text in direct):
            candidates.append(table)
    if len(candidates) != 1:
        raise v2.LayoutDetectionError("May semantic data table not found uniquely")
    table = candidates[0]
    if v1._row_signature(table.rows[0]) != ((v1.UNIT_MARKER, 1, 6),):
        raise TransitionContractError("May unit row must span six columns")
    notes = [row for row in table.rows[5:] if len(row.cells) == 1 and row.cells[0].text.startswith(("Note:", "Notes:"))]
    if len(notes) != 1 or notes[0].cells[0].colspan != 6 or table.rows[-1] is not notes[0]:
        raise TransitionContractError("May requires one terminal six-column note")
    data_rows = tuple(table.rows[5:-1])
    if not data_rows or any(len(row.cells) != 6 or any(cell.rowspan != 1 or cell.colspan != 1 for cell in row.cells) for row in data_rows):
        raise TransitionContractError("May economic rows must contain six plain cells")
    return _MayTable(
        role, published_title, ordinal, publication_date,
        datetime.strptime(publication_date, "%Y-%m-%d").strftime("%B %Y"),
        tuple(table.rows[:5]), data_rows, notes[0].cells[0].text,
        source_url, source_sha256,
    )


def _may_dates(table: _MayTable) -> _MayDates:
    second = table.header_rows[2].cells
    third = table.header_rows[3].cells
    if len(second) != 4 or len(third) != 4 or second[1].colspan != 2:
        raise TransitionContractError("May date header has an unsupported shape")
    base = v1._parse_iso_date(second[0].text, context="combined base")
    year = second[1].text
    prior_period = v1._combine_month_day_year(third[0].text, year, context="prior period")
    current = v1._combine_month_day_year(third[1].text, year, context="current")
    return _MayDates(base, prior_period, current, third[2].text)


def _may_signatures(major: _MayTable, industry: _MayTable) -> v2.ContractSignatures:
    def header(table: _MayTable) -> list[list[tuple[str, int, int]]]:
        return [[(v2._header_token(cell.text), cell.rowspan, cell.colspan) for cell in row.cells] for row in table.header_rows]
    structural = {
        "titles": [major.published_title, industry.published_title],
        "headers": {"major": header(major), "industry": header(industry)},
        "exact_label_presentation": {
            table.role: [row.cells[0].text for row in table.data_rows]
            for table in (major, industry)
        },
        "note_colspan": 6,
    }
    taxonomy = {
        role: [
            {
                "code": mapping.source_row_code,
                "normalized_label": mapping.normalized_label,
                "parent": mapping.parent_row_code,
                "memorandum": mapping.is_memorandum,
            }
            for mapping in MAPPINGS_BY_PERIOD["May 2026"][role]
        ]
        for role in ("major", "industry")
    }
    return v2.ContractSignatures(
        _sha_json(structural),
        _sha_json(taxonomy),
        _sha_json(_methodology_detail(f"{major.note} {industry.note}")),
    )


def _inspect_transition_pair(
    major_html: bytes | str,
    industry_html: bytes | str,
    *,
    major_url: str,
    industry_url: str,
) -> tuple[str, Any, Any, v2.ContractSignatures]:
    major_text, major_hash = v2._prepare_input(major_html, name="major_sectors_html")
    industry_text, industry_hash = v2._prepare_input(industry_html, name="industries_html")
    # The unit colspan distinguishes May before any parser is attempted.
    parser = v1._SemanticTableParser()
    parser.feed(major_text)
    unit_spans = {
        cell.colspan
        for table in parser.tables
        for row in table.rows
        for cell in row.cells
        if cell.text == v1.UNIT_MARKER
    }
    if 6 in unit_spans:
        major = _extract_may(major_text, role="major", source_url=major_url, source_sha256=major_hash)
        industry = _extract_may(industry_text, role="industry", source_url=industry_url, source_sha256=industry_hash)
        period = major.bulletin_period
        if period != "May 2026" or industry.bulletin_period != period:
            raise v2.LayoutDetectionError("Six-column layout is supported only for May 2026")
        _validate_common_pair(major, industry, period)
        return period, major, industry, _may_signatures(major, industry)
    major = v2._extract_contract_table(major_text, expected_role="major", source_url=major_url, source_sha256=major_hash)
    industry = v2._extract_contract_table(industry_text, expected_role="industry", source_url=industry_url, source_sha256=industry_hash)
    if major.bulletin_period != industry.bulletin_period:
        raise TransitionContractError("Table periods disagree")
    period = major.bulletin_period
    if period not in SUPPORTED_PERIODS:
        raise v2.LayoutDetectionError(f"Not a transition period: {period}")
    _validate_common_pair(major, industry, period)
    return period, major, industry, _transition_signatures(major, industry, period)


def detect_transition_layout(
    major_html: bytes | str,
    industry_html: bytes | str,
    *,
    major_url: str,
    industry_url: str,
) -> DetectedLayout:
    period, _major, _industry, signatures = _inspect_transition_pair(
        major_html, industry_html, major_url=major_url, industry_url=industry_url
    )
    return DetectedLayout(_layout_for_period(period), signatures, period)


def _publication_period(html: bytes | str, *, semantic_title: str) -> str:
    text, _digest = v2._prepare_input(html, name="layout_detection_html")
    parser = v1._SemanticTableParser()
    parser.feed(text)
    dates = []
    for table in parser.tables:
        direct = v1._direct_cell_texts(table)
        if not any(semantic_title in value for value in direct):
            continue
        dates.extend(value for value in direct if re.fullmatch(r"Date\s*:\s*.+", value))
    if len(dates) != 1:
        raise v2.LayoutDetectionError("Expected one publication date during positive inspection")
    value = v1._parse_iso_date(re.sub(r"^Date\s*:\s*", "", dates[0]), context="publication")
    return datetime.strptime(value, "%Y-%m-%d").strftime("%B %Y")


def _select_unique_layout(matches: Sequence[DetectedLayout]) -> DetectedLayout:
    if not matches:
        raise v2.LayoutDetectionError("No supported layout positively matched")
    if len(matches) > 1:
        raise v2.AmbiguousLayoutDetectionError(
            f"Multiple supported layouts positively matched: {[item.layout_id for item in matches]!r}"
        )
    return matches[0]


def detect_supported_layout(
    major_html: bytes | str,
    industry_html: bytes | str,
    *,
    major_url: str,
    industry_url: str,
) -> DetectedLayout:
    """Inspect and positively identify v2, transition, or June-2026 v1."""
    major_period = _publication_period(major_html, semantic_title=v2.MAJOR_SEMANTIC_TITLE)
    industry_period = _publication_period(
        industry_html, semantic_title=v2.INDUSTRY_SEMANTIC_TITLE
    )
    if major_period != industry_period:
        raise TransitionContractError("Layout-inspection publication periods disagree")
    matches: list[DetectedLayout] = []
    if major_period in SUPPORTED_PERIODS:
        matches.append(
            detect_transition_layout(
                major_html,
                industry_html,
                major_url=major_url,
                industry_url=industry_url,
            )
        )
    elif major_period in v2.SUPPORTED_BULLETIN_PERIODS or major_period == "June 2026":
        layout_id, signatures = v2.detect_sectoral_credit_layout(
            major_html,
            industry_html,
            major_sectors_url=major_url,
            industries_url=industry_url,
        )
        expected = v2.LAYOUT_ID if major_period in v2.SUPPORTED_BULLETIN_PERIODS else v1.LAYOUT_ID
        if layout_id == expected:
            matches.append(DetectedLayout(layout_id, signatures, major_period))
    return _select_unique_layout(matches)


def _metadata(
    *,
    observations: pd.DataFrame,
    layout_id: str,
    major: Any,
    industry: Any,
    dates: Any,
    override: str | None,
    signatures: v2.ContractSignatures,
    mapped_counts: tuple[tuple[str, int], ...],
    emitted_counts: tuple[tuple[str, int], ...],
    growth_checks: int,
    growth_skipped: int,
) -> v2.ReleaseMetadata:
    duplicate_count = int(observations.duplicated(list(v2.VINTAGE_KEY_COLUMNS), keep=False).sum())
    if duplicate_count:
        raise v1.DataValidationError("Duplicate transition vintage keys")
    return v2.ReleaseMetadata(
        dataset_id=v1.DATASET_ID,
        layout_id=layout_id,
        parser_version=PARSER_VERSION,
        publication_date=major.publication_date,
        bulletin_period=major.bulletin_period,
        current_observation_date=dates.current_observation_date,
        prior_year_reference_date=(
            dates.prior_year_reference_date
            if hasattr(dates, "prior_year_reference_date")
            else dates.combined_base_date
        ),
        financial_year_base_date=(
            dates.financial_year_base_date
            if hasattr(dates, "financial_year_base_date")
            else dates.combined_base_date
        ),
        section42_prior_year_override_date=override,
        source_hashes=(("major", major.source_sha256), ("industry", industry.source_sha256)),
        structural_signature_sha256=signatures.structural_signature_sha256,
        taxonomy_signature_sha256=signatures.taxonomy_signature_sha256,
        methodology_signature_sha256=signatures.methodology_signature_sha256,
        mapped_row_counts=mapped_counts,
        emitted_row_counts=emitted_counts,
        supplemental_non_emitting_row_counts=(("major", 0), ("industry", 0)),
        ignored_empty_row_counts=(("major", 0), ("industry", 0)),
        duplicate_source_column_count=0,
        duplicate_source_row_count=1,
        growth_reconciliation_checks=growth_checks,
        growth_reconciliation_skipped=growth_skipped,
        growth_reconciliation_failures=0,
        canonical_release_key_duplicate_count=duplicate_count,
        observation_counts_by_measure=v2._count_values(observations, "measure"),
        observation_counts_by_population=v2._count_values(observations, "population_id"),
        observation_counts_by_column_role=v2._count_values(observations, "column_role"),
        unique_series_count=int(observations["series_id"].nunique()),
        coverage_percentage="95",
        reporting_bank_count=None,
        release_semantic_sha256=v2.release_semantic_sha256(observations),
        provenance_bound_output_sha256=v2.release_provenance_sha256(observations),
    )


def _parse_four_outstanding(
    period: str,
    major: Any,
    industry: Any,
    signatures: v2.ContractSignatures,
) -> v2.ParsedSectoralCreditRelease:
    dates = v2._parse_header_dates(major)
    if dates != v2._parse_header_dates(industry):
        raise TransitionContractError("Transition header dates disagree")
    mappings = MAPPINGS_BY_PERIOD[period]
    major_rows, major_dupes = v2._map_rows(major, mappings["major"])
    industry_rows, industry_dupes = v2._map_rows(industry, mappings["industry"])
    if major_dupes or industry_dupes:
        raise TransitionContractError("Four-outstanding transition layout cannot repeat columns")
    major_total = next(row for row in major_rows if row.mapping.source_row_code == "2")
    industry_total = next(row for row in industry_rows if row.mapping.source_row_code == "2")
    if major_total.values != industry_total.values:
        raise v1.DataValidationError("Cross-table duplicate Industry row disagrees")
    override = _extract_section42_override(major.note, dates.prior_year_reference_date, column="2")
    checks, skipped = v2._reconcile_growth(
        (*major_rows, *(row for row in industry_rows if row.mapping.source_row_code != "2"))
    )
    layout_id = _layout_for_period(period)
    major_obs, major_emitted = v2._rows_to_observations(
        major, major_rows, dates, section42_override=override,
        layout_id=layout_id, parser_version=PARSER_VERSION,
    )
    industry_obs, industry_emitted = v2._rows_to_observations(
        industry, industry_rows, dates, section42_override=override,
        skip_source_row_code="2", layout_id=layout_id, parser_version=PARSER_VERSION,
    )
    observations = pd.DataFrame([*major_obs, *industry_obs], columns=v2.RELEASE_OBSERVATION_COLUMNS)
    metadata = _metadata(
        observations=observations, layout_id=layout_id, major=major, industry=industry,
        dates=dates, override=override, signatures=signatures,
        mapped_counts=(("major", len(major_rows)), ("industry", len(industry_rows))),
        emitted_counts=(("major", major_emitted), ("industry", industry_emitted)),
        growth_checks=checks, growth_skipped=skipped,
    )
    return v2.ParsedSectoralCreditRelease(
        observations, metadata,
        (v1.SourceNote(major.published_title, major.note), v1.SourceNote(industry.published_title, industry.note)),
    )


def _map_may(table: _MayTable) -> tuple[_MayRow, ...]:
    mappings = MAPPINGS_BY_PERIOD["May 2026"][table.role]
    labels = [row.cells[0].text for row in table.data_rows]
    expected = [mapping.source_label for mapping in mappings]
    if labels != expected:
        raise v1.UnmappedSeriesError(f"{table.published_title}: May source mapping mismatch")
    result = []
    for raw, mapping in zip(table.data_rows, mappings, strict=True):
        values = tuple(
            v1._parse_numeric(cell.text, context=f"May {table.role} {mapping.source_label} column {index}")
            for index, cell in enumerate(raw.cells[1:], start=1)
        )
        if mapping.emits_observations:
            pass
        elif any(value is not None for value in values):
            raise v1.DataValidationError("May structural row unexpectedly contains values")
        result.append(_MayRow(mapping, values))
    return tuple(result)


def _reconcile_may(rows: Iterable[_MayRow]) -> tuple[int, int]:
    checks = skipped = 0
    for row in rows:
        if not row.mapping.emits_observations:
            continue
        for name, reported in (("financial-year", row.values[3]), ("year-on-year", row.values[4])):
            base, current = row.values[0], row.values[2]
            if base is None or current is None or reported is None or base == 0:
                skipped += 1
                continue
            checks += 1
            implied = ((current / base) - Decimal(1)) * Decimal(100)
            if abs(implied - reported) > v1.ROUNDING_TOLERANCE:
                raise v1.DataValidationError(f"May growth reconciliation failed for {row.mapping.source_label!r} {name}")
    return checks, skipped


def _may_observations(
    table: _MayTable,
    rows: Sequence[_MayRow],
    dates: _MayDates,
    *,
    override: str | None,
    skip_code: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    output: list[dict[str, Any]] = []
    emitted = 0
    for may_row in rows:
        mapping = may_row.mapping
        if not mapping.emits_observations or mapping.source_row_code == skip_code:
            continue
        emitted += 1
        base_date = override if table.role == "major" and mapping.population_id == v1.POPULATION_ALL and override else dates.combined_base_date
        adapter = v2._ParsedMappedRow(mapping, may_row.values)
        common = {
            "source": table, "row": adapter,
            "publication_date": table.publication_date, "bulletin_period": table.bulletin_period,
            "layout_id": LAYOUT_MAY, "parser_version": PARSER_VERSION,
        }
        output.extend([
            v2._release_observation(**common, measure=v1.MEASURE_OUTSTANDING, observation_date=base_date, comparison_date=None, value=may_row.values[0], unit=v1.UNIT_INR_CRORE, column_role=FINANCIAL_YEAR_AND_PRIOR_YEAR_REFERENCE),
            v2._release_observation(**common, measure=v1.MEASURE_OUTSTANDING, observation_date=dates.prior_period_reference_date, comparison_date=None, value=may_row.values[1], unit=v1.UNIT_INR_CRORE, column_role=v2.PRIOR_PERIOD_REFERENCE),
            v2._release_observation(**common, measure=v1.MEASURE_OUTSTANDING, observation_date=dates.current_observation_date, comparison_date=None, value=may_row.values[2], unit=v1.UNIT_INR_CRORE, column_role=v2.CURRENT_OBSERVATION),
            v2._release_observation(**common, measure=v1.MEASURE_FY_GROWTH, observation_date=dates.current_observation_date, comparison_date=base_date, value=may_row.values[3], unit=v1.UNIT_PERCENT, column_role=v2.REPORTED_FINANCIAL_YEAR_GROWTH),
            v2._release_observation(**common, measure=v1.MEASURE_YOY_GROWTH, observation_date=dates.current_observation_date, comparison_date=base_date, value=may_row.values[4], unit=v1.UNIT_PERCENT, column_role=v2.REPORTED_YOY_GROWTH),
        ])
    return output, emitted


def _parse_may(
    major: _MayTable, industry: _MayTable, signatures: v2.ContractSignatures
) -> v2.ParsedSectoralCreditRelease:
    dates = _may_dates(major)
    if dates != _may_dates(industry):
        raise TransitionContractError("May table dates disagree")
    major_rows = _map_may(major)
    industry_rows = _map_may(industry)
    major_total = next(row for row in major_rows if row.mapping.source_row_code == "2")
    industry_total = next(row for row in industry_rows if row.mapping.source_row_code == "2")
    if major_total.values != industry_total.values:
        raise v1.DataValidationError("Cross-table duplicate Industry row disagrees")
    override = _extract_section42_override(major.note, dates.combined_base_date, column="1")
    if override is None:
        raise TransitionContractError("May Section-42 column (1) override is required")
    checks, skipped = _reconcile_may(
        (*major_rows, *(row for row in industry_rows if row.mapping.source_row_code != "2"))
    )
    major_obs, major_emitted = _may_observations(major, major_rows, dates, override=override)
    industry_obs, industry_emitted = _may_observations(industry, industry_rows, dates, override=override, skip_code="2")
    observations = pd.DataFrame([*major_obs, *industry_obs], columns=v2.RELEASE_OBSERVATION_COLUMNS)
    metadata = _metadata(
        observations=observations, layout_id=LAYOUT_MAY, major=major, industry=industry,
        dates=dates, override=override, signatures=signatures,
        mapped_counts=(("major", len(major_rows)), ("industry", len(industry_rows))),
        emitted_counts=(("major", major_emitted), ("industry", industry_emitted)),
        growth_checks=checks, growth_skipped=skipped,
    )
    return v2.ParsedSectoralCreditRelease(
        observations, metadata,
        (v1.SourceNote(major.published_title, major.note), v1.SourceNote(industry.published_title, industry.note)),
    )


def parse_transition_release(
    major_html: bytes | str,
    industry_html: bytes | str,
    *,
    major_url: str,
    industry_url: str,
) -> v2.ParsedSectoralCreditRelease:
    period, major, industry, signatures = _inspect_transition_pair(
        major_html, industry_html, major_url=major_url, industry_url=industry_url
    )
    if period == "May 2026":
        return _parse_may(major, industry, signatures)
    return _parse_four_outstanding(period, major, industry, signatures)


def v1_release_observations(parsed: v1.ParsedSectoralCredit) -> pd.DataFrame:
    """Convert the June v1 snapshot result to the common vintage staging schema."""
    current = parsed.metadata.current_observation_date
    outstanding = parsed.observations.loc[
        parsed.observations["measure"] == v1.MEASURE_OUTSTANDING
    ]
    candidate_counts = outstanding.loc[
        outstanding["observation_date"] != current, "observation_date"
    ].value_counts()
    if candidate_counts.empty:
        raise v1.DataValidationError("Cannot identify the v1 financial-year base date")
    financial_base = str(candidate_counts.index[0])
    rows = []
    for record in parsed.observations.to_dict("records"):
        measure = record["measure"]
        if measure == v1.MEASURE_FY_GROWTH:
            role = v2.REPORTED_FINANCIAL_YEAR_GROWTH
        elif measure == v1.MEASURE_YOY_GROWTH:
            role = v2.REPORTED_YOY_GROWTH
        elif record["observation_date"] == current:
            role = v2.CURRENT_OBSERVATION
        elif record["observation_date"] == financial_base:
            role = v2.FINANCIAL_YEAR_BASE
        else:
            role = v2.PRIOR_YEAR_REFERENCE
        record["column_role"] = role
        rows.append({column: record[column] for column in v2.RELEASE_OBSERVATION_COLUMNS})
    frame = pd.DataFrame(rows, columns=v2.RELEASE_OBSERVATION_COLUMNS)
    if frame.duplicated(list(v2.VINTAGE_KEY_COLUMNS)).any():
        raise v1.DataValidationError("June v1 conversion produced duplicate vintage keys")
    return frame


__all__ = [
    "BOUNDARY_CLASSIFICATIONS",
    "COMPARABILITY_CLASSES",
    "FINANCIAL_YEAR_AND_PRIOR_YEAR_REFERENCE",
    "LAYOUT_FEBRUARY_APRIL",
    "LAYOUT_JANUARY",
    "LAYOUT_MAY",
    "MAPPINGS_BY_PERIOD",
    "PARSER_VERSION",
    "SUPPORTED_PERIODS",
    "TransitionContractError",
    "detect_transition_layout",
    "detect_supported_layout",
    "parse_transition_release",
    "v1_release_observations",
]
