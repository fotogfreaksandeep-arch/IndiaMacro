"""Pure parser for RBI Bulletin sectoral-credit tables 15 and 16.

The supported contract is documented in
``docs/contracts/rbi_sectoral_credit_bulletin_v1.md``. This module performs no
network or filesystem access.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
from typing import Any, Final, Iterable

import pandas as pd


DATASET_ID: Final = "RBI_SECTORAL_CREDIT"
LAYOUT_ID: Final = "RBI_BULLETIN_SECTORAL_CREDIT_V1"
PARSER_VERSION: Final = "1.0.0"
MAX_INPUT_BYTES: Final = 5 * 1024 * 1024

MAJOR_TITLE: Final = "15. Deployment of Gross Bank Credit by Major Sectors"
INDUSTRY_TITLE: Final = "16. Industry-wise Deployment of Gross Bank Credit"
UNIT_MARKER: Final = "(₹ Crore)"

POPULATION_ALL: Final = "ALL_SCBS_SECTION42"
POPULATION_SELECT: Final = "SELECT_SCBS_SIBC"

MEASURE_OUTSTANDING: Final = "OUTSTANDING"
MEASURE_FY_GROWTH: Final = "FINANCIAL_YEAR_GROWTH_REPORTED"
MEASURE_YOY_GROWTH: Final = "YOY_GROWTH_REPORTED"

UNIT_INR_CRORE: Final = "INR_CRORE"
UNIT_PERCENT: Final = "PERCENT"

OBSERVATION_COLUMNS: Final = (
    "dataset_id",
    "series_id",
    "source_table",
    "source_row_code",
    "source_label",
    "parent_row_code",
    "sector_level_1",
    "sector_level_2",
    "sector_level_3",
    "measure",
    "observation_date",
    "comparison_date",
    "value",
    "unit",
    "population_id",
    "publication_date",
    "bulletin_period",
    "is_provisional",
    "is_memorandum",
    "footnote_references",
    "source_url",
    "source_sha256",
    "parser_version",
    "layout_id",
)
SEMANTIC_OBSERVATION_COLUMNS: Final = OBSERVATION_COLUMNS[:20]
SEMANTIC_MISSING_VALUE: Final = r"\N"

MISSING_MARKERS: Final = frozenset({"", "-", "–", "—", "..", "...", "NA", "N.A."})
ROUNDING_TOLERANCE: Final = Decimal("0.11")


class SectoralCreditParseError(ValueError):
    """Base class for a contract or data failure."""


class UnsupportedLayoutError(SectoralCreditParseError):
    """The source does not match the supported semantic layout."""


class DataValidationError(SectoralCreditParseError):
    """Source values or dates violate the parsing contract."""


class AmbiguousTableError(UnsupportedLayoutError):
    """A semantic table selector matched more than one table."""


class UnmappedSeriesError(UnsupportedLayoutError):
    """An economic row is unknown or a required mapped row is absent."""


@dataclass(frozen=True)
class SourceNote:
    source_table: str
    text: str


@dataclass(frozen=True)
class ParseMetadata:
    dataset_id: str
    layout_id: str
    parser_version: str
    source_hashes: tuple[tuple[str, str], ...]
    publication_date: str
    bulletin_period: str
    current_observation_date: str
    data_row_counts: tuple[tuple[str, int], ...]
    mapped_row_counts: tuple[tuple[str, int], ...]
    emitted_row_counts: tuple[tuple[str, int], ...]
    observation_counts_by_measure: tuple[tuple[str, int], ...]
    observation_counts_by_population: tuple[tuple[str, int], ...]
    unknown_row_count: int
    duplicate_source_column_count: int
    duplicate_source_row_count: int
    duplicate_source_rows: tuple[tuple[str, str, str, str], ...]
    canonical_duplicate_key_count: int
    growth_reconciliation_checks: int
    growth_reconciliation_skipped: int
    growth_reconciliation_failures: int
    growth_reconciliation_tolerance_percentage_points: str
    layout_signature_sha256: str
    semantic_observations_sha256: str
    provenance_bound_output_sha256: str


@dataclass(frozen=True)
class ParsedSectoralCredit:
    observations: pd.DataFrame
    metadata: ParseMetadata
    notes: tuple[SourceNote, ...]


@dataclass(frozen=True)
class _RowSpec:
    source_label: str
    source_row_code: str
    base_series_id: str
    parent_row_code: str | None
    sector_level_1: str | None
    sector_level_2: str | None
    sector_level_3: str | None
    population_id: str
    is_memorandum: bool = False
    footnotes: tuple[str, ...] = ()
    emits_observations: bool = True


def _spec(
    source_label: str,
    source_row_code: str,
    base_series_id: str,
    parent_row_code: str | None,
    level_1: str | None,
    level_2: str | None = None,
    level_3: str | None = None,
    *,
    population: str = POPULATION_SELECT,
    memo: bool = False,
    footnotes: tuple[str, ...] = (),
    emits: bool = True,
) -> _RowSpec:
    return _RowSpec(
        source_label,
        source_row_code,
        base_series_id,
        parent_row_code,
        level_1,
        level_2,
        level_3,
        population,
        memo,
        footnotes,
        emits,
    )


MAJOR_ROW_SPECS: Final = (
    _spec("I. Bank Credit (II + III)", "I", "BANK_CREDIT", None, "Bank Credit", population=POPULATION_ALL),
    _spec("II. Food Credit", "II", "FOOD_CREDIT", "I", "Food Credit", population=POPULATION_ALL),
    _spec("III. Non-food Credit", "III", "NON_FOOD_CREDIT", "I", "Non-food Credit", population=POPULATION_ALL),
    _spec("1. Agriculture & Allied Activities", "1", "AGRICULTURE_ALLIED", "III", "Agriculture & Allied Activities"),
    _spec("2. Industry (Micro and Small, Medium and Large)", "2", "INDUSTRY", "III", "Industry"),
    _spec("2.1 Micro and Small", "2.1", "INDUSTRY.MICRO_SMALL", "2", "Industry", "Micro and Small"),
    _spec("2.2 Medium", "2.2", "INDUSTRY.MEDIUM", "2", "Industry", "Medium"),
    _spec("2.3 Large", "2.3", "INDUSTRY.LARGE", "2", "Industry", "Large"),
    _spec("3. Services", "3", "SERVICES", "III", "Services"),
    _spec("3.1 Transport Operators", "3.1", "SERVICES.TRANSPORT_OPERATORS", "3", "Services", "Transport Operators"),
    _spec("3.2 Computer Software", "3.2", "SERVICES.COMPUTER_SOFTWARE", "3", "Services", "Computer Software"),
    _spec("3.3 Tourism, Hotels & Restaurants", "3.3", "SERVICES.TOURISM_HOTELS_RESTAURANTS", "3", "Services", "Tourism, Hotels & Restaurants"),
    _spec("3.4 Shipping", "3.4", "SERVICES.SHIPPING", "3", "Services", "Shipping"),
    _spec("3.5 Aviation", "3.5", "SERVICES.AVIATION", "3", "Services", "Aviation"),
    _spec("3.6 Professional Services", "3.6", "SERVICES.PROFESSIONAL", "3", "Services", "Professional Services"),
    _spec("3.7 Trade", "3.7", "SERVICES.TRADE", "3", "Services", "Trade"),
    _spec("3.7.1. Wholesale Trade¹", "3.7.1", "SERVICES.TRADE.WHOLESALE", "3.7", "Services", "Trade", "Wholesale Trade", footnotes=("1",)),
    _spec("3.7.2 Retail Trade", "3.7.2", "SERVICES.TRADE.RETAIL", "3.7", "Services", "Trade", "Retail Trade"),
    _spec("3.8 Commercial Real Estate", "3.8", "SERVICES.COMMERCIAL_REAL_ESTATE", "3", "Services", "Commercial Real Estate"),
    _spec("3.9 Non-Banking Financial Companies (NBFCs)² of which,", "3.9", "SERVICES.NBFCS", "3", "Services", "Non-Banking Financial Companies (NBFCs)", footnotes=("2",)),
    _spec("3.9.1 Housing Finance Companies (HFCs)", "3.9.1", "SERVICES.NBFCS.HFCS", "3.9", "Services", "Non-Banking Financial Companies (NBFCs)", "Housing Finance Companies (HFCs)"),
    _spec("3.9.2 Public Financial Institutions (PFIs)", "3.9.2", "SERVICES.NBFCS.PFIS", "3.9", "Services", "Non-Banking Financial Companies (NBFCs)", "Public Financial Institutions (PFIs)"),
    _spec("3.10 Other Services³", "3.10", "SERVICES.OTHER", "3", "Services", "Other Services", footnotes=("3",)),
    _spec("4. Personal Loans", "4", "PERSONAL_LOANS", "III", "Personal Loans"),
    _spec("4.1 Consumer Durables", "4.1", "PERSONAL_LOANS.CONSUMER_DURABLES", "4", "Personal Loans", "Consumer Durables"),
    _spec("4.2 Housing", "4.2", "PERSONAL_LOANS.HOUSING", "4", "Personal Loans", "Housing"),
    _spec("4.3 Advances against Fixed Deposits", "4.3", "PERSONAL_LOANS.ADVANCES_FIXED_DEPOSITS", "4", "Personal Loans", "Advances against Fixed Deposits"),
    _spec("4.4 Advances to Individuals against share & bonds", "4.4", "PERSONAL_LOANS.ADVANCES_SHARES_BONDS", "4", "Personal Loans", "Advances to Individuals against share & bonds"),
    _spec("4.5 Credit Card Outstanding", "4.5", "PERSONAL_LOANS.CREDIT_CARD_OUTSTANDING", "4", "Personal Loans", "Credit Card Outstanding"),
    _spec("4.6 Education", "4.6", "PERSONAL_LOANS.EDUCATION", "4", "Personal Loans", "Education"),
    _spec("4.7 Vehicle Loans", "4.7", "PERSONAL_LOANS.VEHICLE_LOANS", "4", "Personal Loans", "Vehicle Loans"),
    _spec("4.8 Loan against gold jewellery 4", "4.8", "PERSONAL_LOANS.GOLD_JEWELLERY", "4", "Personal Loans", "Loan against gold jewellery", footnotes=("4",)),
    _spec("4.9 Other Personal Loans", "4.9", "PERSONAL_LOANS.OTHER", "4", "Personal Loans", "Other Personal Loans"),
    _spec("5. Priority Sector (Memo)", "5", "PRIORITY_SECTOR_MEMO", None, "Priority Sector (Memo)", memo=True, emits=False),
    _spec("(i) Agriculture & Allied Activities 5", "(i)", "PRIORITY.AGRICULTURE_ALLIED", "5", "Priority Sector (Memo)", "Agriculture & Allied Activities", memo=True, footnotes=("5",)),
    _spec("(ii) Micro & Small Enterprises 6", "(ii)", "PRIORITY.MICRO_SMALL_ENTERPRISES", "5", "Priority Sector (Memo)", "Micro & Small Enterprises", memo=True, footnotes=("6",)),
    _spec("(iii) Medium Enterprises 7", "(iii)", "PRIORITY.MEDIUM_ENTERPRISES", "5", "Priority Sector (Memo)", "Medium Enterprises", memo=True, footnotes=("7",)),
    _spec("(iv) Housing", "(iv)", "PRIORITY.HOUSING", "5", "Priority Sector (Memo)", "Housing", memo=True),
    _spec("(v) Education Loans", "(v)", "PRIORITY.EDUCATION_LOANS", "5", "Priority Sector (Memo)", "Education Loans", memo=True),
    _spec("(vi) Renewable Energy", "(vi)", "PRIORITY.RENEWABLE_ENERGY", "5", "Priority Sector (Memo)", "Renewable Energy", memo=True),
    _spec("(vii) Social Infrastructure", "(vii)", "PRIORITY.SOCIAL_INFRASTRUCTURE", "5", "Priority Sector (Memo)", "Social Infrastructure", memo=True),
    _spec("(viii) Export Credit", "(viii)", "PRIORITY.EXPORT_CREDIT", "5", "Priority Sector (Memo)", "Export Credit", memo=True),
    _spec("(ix) Others", "(ix)", "PRIORITY.OTHERS", "5", "Priority Sector (Memo)", "Others", memo=True),
    _spec("(x) Weaker Sections including net PSLC- SF/MF", "(x)", "PRIORITY.WEAKER_SECTIONS_NET_PSLC", "5", "Priority Sector (Memo)", "Weaker Sections including net PSLC- SF/MF", memo=True),
)


INDUSTRY_ROW_SPECS: Final = (
    _spec("2. Industries (2.1 to 2.19)", "2", "INDUSTRY", "III", "Industry"),
    _spec("2.1 Mining & Quarrying (incl. Coal)", "2.1", "INDUSTRY.MINING_QUARRYING", "2", "Industry", "Mining & Quarrying (incl. Coal)"),
    _spec("2.2 Food Processing", "2.2", "INDUSTRY.FOOD_PROCESSING", "2", "Industry", "Food Processing"),
    _spec("2.2.1 Sugar", "2.2.1", "INDUSTRY.FOOD_PROCESSING.SUGAR", "2.2", "Industry", "Food Processing", "Sugar"),
    _spec("2.2.2 Edible Oils & Vanaspati", "2.2.2", "INDUSTRY.FOOD_PROCESSING.EDIBLE_OILS_VANASPATI", "2.2", "Industry", "Food Processing", "Edible Oils & Vanaspati"),
    _spec("2.2.3 Tea", "2.2.3", "INDUSTRY.FOOD_PROCESSING.TEA", "2.2", "Industry", "Food Processing", "Tea"),
    _spec("2.2.4 Others", "2.2.4", "INDUSTRY.FOOD_PROCESSING.OTHERS", "2.2", "Industry", "Food Processing", "Others"),
    _spec("2.3 Beverage & Tobacco", "2.3", "INDUSTRY.BEVERAGE_TOBACCO", "2", "Industry", "Beverage & Tobacco"),
    _spec("2.4 Textiles", "2.4", "INDUSTRY.TEXTILES", "2", "Industry", "Textiles"),
    _spec("2.4.1 Cotton Textiles", "2.4.1", "INDUSTRY.TEXTILES.COTTON", "2.4", "Industry", "Textiles", "Cotton Textiles"),
    _spec("2.4.2 Jute Textiles", "2.4.2", "INDUSTRY.TEXTILES.JUTE", "2.4", "Industry", "Textiles", "Jute Textiles"),
    _spec("2.4.3 Man-Made Textiles", "2.4.3", "INDUSTRY.TEXTILES.MAN_MADE", "2.4", "Industry", "Textiles", "Man-Made Textiles"),
    _spec("2.4.4 Other Textiles", "2.4.4", "INDUSTRY.TEXTILES.OTHER", "2.4", "Industry", "Textiles", "Other Textiles"),
    _spec("2.5 Leather & Leather Products", "2.5", "INDUSTRY.LEATHER_PRODUCTS", "2", "Industry", "Leather & Leather Products"),
    _spec("2.6 Wood & Wood Products", "2.6", "INDUSTRY.WOOD_PRODUCTS", "2", "Industry", "Wood & Wood Products"),
    _spec("2.7 Paper & Paper Products", "2.7", "INDUSTRY.PAPER_PRODUCTS", "2", "Industry", "Paper & Paper Products"),
    _spec("2.8 Petroleum, Coal Products & Nuclear Fuels", "2.8", "INDUSTRY.PETROLEUM_COAL_NUCLEAR", "2", "Industry", "Petroleum, Coal Products & Nuclear Fuels"),
    _spec("2.9 Chemicals & Chemical Products", "2.9", "INDUSTRY.CHEMICALS", "2", "Industry", "Chemicals & Chemical Products"),
    _spec("2.9.1 Fertiliser", "2.9.1", "INDUSTRY.CHEMICALS.FERTILISER", "2.9", "Industry", "Chemicals & Chemical Products", "Fertiliser"),
    _spec("2.9.2 Drugs & Pharmaceuticals", "2.9.2", "INDUSTRY.CHEMICALS.DRUGS_PHARMA", "2.9", "Industry", "Chemicals & Chemical Products", "Drugs & Pharmaceuticals"),
    _spec("2.9.3 Petro Chemicals", "2.9.3", "INDUSTRY.CHEMICALS.PETRO_CHEMICALS", "2.9", "Industry", "Chemicals & Chemical Products", "Petro Chemicals"),
    _spec("2.9.4 Others", "2.9.4", "INDUSTRY.CHEMICALS.OTHERS", "2.9", "Industry", "Chemicals & Chemical Products", "Others"),
    _spec("2.10 Rubber, Plastic & their Products", "2.10", "INDUSTRY.RUBBER_PLASTIC", "2", "Industry", "Rubber, Plastic & their Products"),
    _spec("2.11 Glass & Glassware", "2.11", "INDUSTRY.GLASS_GLASSWARE", "2", "Industry", "Glass & Glassware"),
    _spec("2.12 Cement & Cement Products", "2.12", "INDUSTRY.CEMENT", "2", "Industry", "Cement & Cement Products"),
    _spec("2.13 Basic Metal & Metal Product", "2.13", "INDUSTRY.BASIC_METAL", "2", "Industry", "Basic Metal & Metal Product"),
    _spec("2.13.1 Iron & Steel", "2.13.1", "INDUSTRY.BASIC_METAL.IRON_STEEL", "2.13", "Industry", "Basic Metal & Metal Product", "Iron & Steel"),
    _spec("2.13.2 Other Metal & Metal Product", "2.13.2", "INDUSTRY.BASIC_METAL.OTHER", "2.13", "Industry", "Basic Metal & Metal Product", "Other Metal & Metal Product"),
    _spec("2.14 All Engineering", "2.14", "INDUSTRY.ALL_ENGINEERING", "2", "Industry", "All Engineering"),
    _spec("2.14.1 Electronics", "2.14.1", "INDUSTRY.ALL_ENGINEERING.ELECTRONICS", "2.14", "Industry", "All Engineering", "Electronics"),
    _spec("2.14.2 Others", "2.14.2", "INDUSTRY.ALL_ENGINEERING.OTHERS", "2.14", "Industry", "All Engineering", "Others"),
    _spec("2.15 Vehicles, Vehicle Parts & Transport Equipment", "2.15", "INDUSTRY.VEHICLES_TRANSPORT_EQUIPMENT", "2", "Industry", "Vehicles, Vehicle Parts & Transport Equipment"),
    _spec("2.16 Gems & Jewellery", "2.16", "INDUSTRY.GEMS_JEWELLERY", "2", "Industry", "Gems & Jewellery"),
    _spec("2.17 Construction", "2.17", "INDUSTRY.CONSTRUCTION", "2", "Industry", "Construction"),
    _spec("2.18 Infrastructure", "2.18", "INDUSTRY.INFRASTRUCTURE", "2", "Industry", "Infrastructure"),
    _spec("2.18.1 Power", "2.18.1", "INDUSTRY.INFRASTRUCTURE.POWER", "2.18", "Industry", "Infrastructure", "Power"),
    _spec("2.18.2 Telecommunications", "2.18.2", "INDUSTRY.INFRASTRUCTURE.TELECOMMUNICATIONS", "2.18", "Industry", "Infrastructure", "Telecommunications"),
    _spec("2.18.3 Roads", "2.18.3", "INDUSTRY.INFRASTRUCTURE.ROADS", "2.18", "Industry", "Infrastructure", "Roads"),
    _spec("2.18.4 Airports", "2.18.4", "INDUSTRY.INFRASTRUCTURE.AIRPORTS", "2.18", "Industry", "Infrastructure", "Airports"),
    _spec("2.18.5 Ports", "2.18.5", "INDUSTRY.INFRASTRUCTURE.PORTS", "2.18", "Industry", "Infrastructure", "Ports"),
    _spec("2.18.6 Railways", "2.18.6", "INDUSTRY.INFRASTRUCTURE.RAILWAYS", "2.18", "Industry", "Infrastructure", "Railways"),
    _spec("2.18.7 Other Infrastructure", "2.18.7", "INDUSTRY.INFRASTRUCTURE.OTHER", "2.18", "Industry", "Infrastructure", "Other Infrastructure"),
    _spec("2.19 Other Industries", "2.19", "INDUSTRY.OTHER", "2", "Industry", "Other Industries"),
)


@dataclass
class _Cell:
    rowspan: int
    colspan: int
    parts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return _normalize(" ".join(self.parts))


@dataclass
class _HtmlRow:
    cells: list[_Cell] = field(default_factory=list)


@dataclass
class _HtmlTable:
    index: int
    parent: _HtmlTable | None
    rows: list[_HtmlRow] = field(default_factory=list)
    children: list[_HtmlTable] = field(default_factory=list)


class _SemanticTableParser(HTMLParser):
    """Retain only bounded table structure."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[_HtmlTable] = []
        self._table_stack: list[_HtmlTable] = []
        self._row_stack: list[tuple[_HtmlTable, _HtmlRow]] = []
        self._cell_stack: list[tuple[_HtmlTable, _Cell]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            parent = self._table_stack[-1] if self._table_stack else None
            table = _HtmlTable(len(self.tables), parent)
            self.tables.append(table)
            if parent is not None:
                parent.children.append(table)
            self._table_stack.append(table)
        elif tag == "tr" and self._table_stack:
            table = self._table_stack[-1]
            row = _HtmlRow()
            table.rows.append(row)
            self._row_stack.append((table, row))
        elif tag in {"td", "th"} and self._table_stack and self._row_stack:
            attrs_dict = dict(attrs) if attrs else {}
            table = self._table_stack[-1]
            row_table, row = self._row_stack[-1]
            if row_table is table:
                try:
                    cell = _Cell(
                        rowspan=int(attrs_dict.get("rowspan") or 1),
                        colspan=int(attrs_dict.get("colspan") or 1),
                    )
                except ValueError as exc:
                    raise UnsupportedLayoutError("Non-integer rowspan or colspan") from exc
                row.cells.append(cell)
                self._cell_stack.append((table, cell))

    def handle_data(self, data: str) -> None:
        text = _normalize(unescape(data))
        if not text:
            return
        if self._cell_stack and self._table_stack:
            cell_table, cell = self._cell_stack[-1]
            if cell_table is self._table_stack[-1]:
                cell.parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._cell_stack:
            self._cell_stack.pop()
        elif tag == "tr" and self._row_stack:
            self._row_stack.pop()
        elif tag == "table" and self._table_stack:
            self._table_stack.pop()


@dataclass(frozen=True)
class _HeaderDates:
    column_1_date: str
    column_2_date: str
    column_3_date: str
    column_4_date: str
    fiscal_year: str


@dataclass(frozen=True)
class _SourceTable:
    title: str
    publication_date: str
    bulletin_period: str
    header_dates: _HeaderDates
    raw_rows: tuple[tuple[str, tuple[str, ...]], ...]
    note: str
    source_url: str
    source_sha256: str


@dataclass(frozen=True)
class _ParsedRow:
    spec: _RowSpec
    values: tuple[Decimal | None, ...]


def _normalize(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _direct_cell_texts(table: _HtmlTable) -> list[str]:
    return [cell.text for row in table.rows for cell in row.cells]


def _descendants(table: _HtmlTable) -> Iterable[_HtmlTable]:
    for child in table.children:
        yield child
        yield from _descendants(child)


def _row_signature(row: _HtmlRow) -> tuple[tuple[str, int, int], ...]:
    return tuple((cell.text, cell.rowspan, cell.colspan) for cell in row.cells)


def _parse_iso_date(value: str, *, context: str) -> str:
    cleaned = value.replace(".", "").replace(",", "")
    try:
        return datetime.strptime(cleaned, "%b %d %Y").date().isoformat()
    except ValueError as exc:
        raise UnsupportedLayoutError(f"Cannot parse {context} date: {value!r}") from exc


def _combine_month_day_year(month_day: str, year: str, *, context: str) -> str:
    return _parse_iso_date(f"{month_day}, {year}", context=context)


def _parse_header(table: _HtmlTable, title: str) -> _HeaderDates:
    if len(table.rows) < 6:
        raise UnsupportedLayoutError(f"{title}: fewer than six header/data rows")
    unit_signature = _row_signature(table.rows[0])
    if unit_signature != ((UNIT_MARKER, 1, 7),):
        raise UnsupportedLayoutError(f"{title}: unsupported unit row {unit_signature!r}")

    first_header = _row_signature(table.rows[1])
    expected_first = {
        MAJOR_TITLE: (
            ("Sector", 4, 1),
            ("Outstanding as on", 1, 3),
            ("Growth (%)", 1, 2),
            ("", 1, 1),
        ),
        INDUSTRY_TITLE: (
            ("Industry", 4, 1),
            ("Outstanding as on", 1, 4),
            ("Growth (%)", 1, 2),
        ),
    }[title]
    if first_header != expected_first:
        raise UnsupportedLayoutError(
            f"{title}: unknown top-level header or data column {first_header!r}"
        )

    second_header = _row_signature(table.rows[2])
    if len(second_header) != 5:
        raise UnsupportedLayoutError(f"{title}: expected five second-level header cells")
    full_date, prior_year, current_year, fy_label, yoy_label = second_header
    if (
        full_date[1:] != (2, 1)
        or not re.fullmatch(r"[A-Z][a-z]{2}\. \d{1,2}, 20\d{2}", full_date[0])
        or prior_year[1:] != (1, 1)
        or not re.fullmatch(r"20\d{2}", prior_year[0])
        or current_year[1:] != (1, 2)
        or not re.fullmatch(r"20\d{2}", current_year[0])
        or fy_label != ("Financial year so far", 1, 1)
        or yoy_label != ("Y-o-Y", 1, 1)
    ):
        raise UnsupportedLayoutError(f"{title}: unsupported second-level header {second_header!r}")

    third_header = _row_signature(table.rows[3])
    if len(third_header) != 5 or any(item[1:] != (1, 1) for item in third_header):
        raise UnsupportedLayoutError(f"{title}: unsupported third-level header {third_header!r}")
    prior_month_day, duplicate_month_day, current_month_day, fiscal_year, yoy_year = (
        item[0] for item in third_header
    )
    month_day_pattern = r"[A-Z][a-z]{2}\. \d{1,2}"
    if (
        not re.fullmatch(month_day_pattern, prior_month_day)
        or not re.fullmatch(month_day_pattern, duplicate_month_day)
        or not re.fullmatch(month_day_pattern, current_month_day)
        or not re.fullmatch(r"20\d{2}-\d{2}", fiscal_year)
        or yoy_year != current_year[0]
    ):
        raise UnsupportedLayoutError(f"{title}: unsupported date/fiscal header {third_header!r}")

    fourth_header = _row_signature(table.rows[4])
    expected_fourth = tuple((text, 1, 1) for text in ("(1)", "(2)", "(3)", "(4)", "%", "%"))
    if fourth_header != expected_fourth:
        raise UnsupportedLayoutError(f"{title}: unknown source columns {fourth_header!r}")

    column_1 = _parse_iso_date(full_date[0], context="column (1)")
    column_2 = _combine_month_day_year(prior_month_day, prior_year[0], context="column (2)")
    column_3 = _combine_month_day_year(
        duplicate_month_day, current_year[0], context="column (3)"
    )
    column_4 = _combine_month_day_year(
        current_month_day, current_year[0], context="column (4)"
    )
    if column_1 != column_3:
        raise UnsupportedLayoutError(
            f"{title}: columns (1) and (3) do not describe the same date"
        )
    return _HeaderDates(column_1, column_2, column_3, column_4, fiscal_year)


def _bulletin_period_from_publication_date(publication_date: str) -> str:
    """Derive the issue period used by this layout from its table publication date."""
    parsed = datetime.strptime(publication_date, "%Y-%m-%d")
    return parsed.strftime("%B %Y")


def _extract_source_table(
    html: str,
    *,
    title: str,
    source_url: str,
    source_sha256: str,
) -> _SourceTable:
    parser = _SemanticTableParser()
    parser.feed(html)

    title_tables = [
        table for table in parser.tables if title in {text for text in _direct_cell_texts(table)}
    ]
    if not title_tables:
        raise UnsupportedLayoutError(f"No table with exact normalized title {title!r}")
    if len(title_tables) > 1:
        raise AmbiguousTableError(f"Multiple tables contain exact title {title!r}")
    title_table = title_tables[0]

    publication_cells = [
        text for text in _direct_cell_texts(title_table) if re.fullmatch(r"Date\s*:\s*.+", text)
    ]
    if len(publication_cells) != 1:
        raise UnsupportedLayoutError(
            f"{title}: expected one publication date, found {publication_cells!r}"
        )
    publication_date = _parse_iso_date(
        re.sub(r"^Date\s*:\s*", "", publication_cells[0]), context="publication"
    )

    data_candidates = []
    for table in _descendants(title_table):
        direct = _direct_cell_texts(table)
        if UNIT_MARKER in direct and "Outstanding as on" in direct and "Growth (%)" in direct:
            data_candidates.append(table)
    if not data_candidates:
        raise UnsupportedLayoutError(f"{title}: matching data table not found")
    if len(data_candidates) > 1:
        raise AmbiguousTableError(f"{title}: multiple matching semantic data tables")
    data_table = data_candidates[0]
    header_dates = _parse_header(data_table, title)

    notes_rows = [
        row
        for row in data_table.rows[5:]
        if len(row.cells) == 1 and row.cells[0].text.startswith(("Note:", "Notes:"))
    ]
    if len(notes_rows) != 1 or notes_rows[0].cells[0].colspan != 7:
        raise UnsupportedLayoutError(f"{title}: expected one seven-column notes row")
    note = notes_rows[0].cells[0].text

    raw_rows: list[tuple[str, tuple[str, ...]]] = []
    for row in data_table.rows[5:]:
        if row is notes_rows[0]:
            break
        if len(row.cells) != 7 or any(cell.rowspan != 1 or cell.colspan != 1 for cell in row.cells):
            raise UnsupportedLayoutError(f"{title}: economic row does not contain seven plain cells")
        raw_rows.append((row.cells[0].text, tuple(cell.text for cell in row.cells[1:])))
    if not raw_rows:
        raise UnsupportedLayoutError(f"{title}: no economic rows")

    return _SourceTable(
        title=title,
        publication_date=publication_date,
        bulletin_period=_bulletin_period_from_publication_date(publication_date),
        header_dates=header_dates,
        raw_rows=tuple(raw_rows),
        note=note,
        source_url=source_url,
        source_sha256=source_sha256,
    )


def _validate_required_notes(major: _SourceTable, industry: _SourceTable) -> str:
    required_major = (
        "Data are provisional",
        "Section-42 return",
        "covers all scheduled commercial banks (SCBs)",
        "sector-wise and industry-wise bank credit (SIBC) return",
        "covers select banks accounting for about 95 per cent",
        "Reference date for Section-42 data (rows I, II & III) in Column (2) is",
    )
    missing = [marker for marker in required_major if marker not in major.note]
    if missing:
        raise UnsupportedLayoutError(f"{MAJOR_TITLE}: required population/note markers missing: {missing}")
    required_method = (
        "With effect from December 31, 2025",
        "last day of the month",
        "corresponding month of the previous year",
    )
    for table in (major, industry):
        absent = [marker for marker in required_method if marker not in table.note]
        if absent:
            raise UnsupportedLayoutError(
                f"{table.title}: required methodology markers missing: {absent}"
            )
    override_match = re.search(
        r"Reference date for Section-42 data \(rows I, II & III\) in Column \(2\) is "
        r"([A-Z][a-z]+ \d{1,2}, 20\d{2})",
        major.note,
    )
    if not override_match:
        raise UnsupportedLayoutError("Section-42 column (2) date override cannot be extracted")
    return _parse_iso_date(override_match.group(1), context="Section-42 override")


def _parse_numeric(value: str, *, context: str) -> Decimal | None:
    normalized = _normalize(value)
    if normalized in MISSING_MARKERS:
        return None
    if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", normalized):
        raise DataValidationError(f"Malformed numeric value {normalized!r} at {context}")
    try:
        return Decimal(normalized.replace(",", ""))
    except InvalidOperation as exc:
        raise DataValidationError(f"Malformed numeric value {normalized!r} at {context}") from exc


def _map_rows(
    source: _SourceTable, specs: tuple[_RowSpec, ...]
) -> tuple[tuple[_ParsedRow, ...], int]:
    mapping = {spec.source_label: spec for spec in specs}
    if len(mapping) != len(specs):
        raise DataValidationError(f"{source.title}: duplicate explicit mapping label")
    source_labels = [label for label, _ in source.raw_rows]
    unknown = [label for label in source_labels if label not in mapping]
    missing = [spec.source_label for spec in specs if spec.source_label not in source_labels]
    if unknown or missing:
        raise UnmappedSeriesError(
            f"{source.title}: unknown rows={unknown!r}; missing required rows={missing!r}"
        )
    if len(source_labels) != len(set(source_labels)):
        raise DataValidationError(f"{source.title}: duplicate source row label")
    expected_labels = [spec.source_label for spec in specs]
    if source_labels != expected_labels:
        raise UnmappedSeriesError(f"{source.title}: mapped rows are not in the supported order")

    parsed: list[_ParsedRow] = []
    duplicate_columns = 0
    for source_label, raw_values in source.raw_rows:
        spec = mapping[source_label]
        values = tuple(
            _parse_numeric(value, context=f"{source.title} row {source_label!r} column {index}")
            for index, value in enumerate(raw_values, start=1)
        )
        if not spec.emits_observations:
            if any(value is not None for value in values):
                raise DataValidationError(
                    f"{source.title} structural row {source_label!r} unexpectedly contains values"
                )
        else:
            if values[0] != values[2]:
                raise DataValidationError(
                    f"{source.title} row {source_label!r}: duplicate columns (1) and (3) disagree"
                )
            duplicate_columns += 1
        parsed.append(_ParsedRow(spec, values))
    return tuple(parsed), duplicate_columns


def _reconcile_growth(rows: Iterable[_ParsedRow]) -> tuple[int, int]:
    checks = 0
    skipped = 0
    for row in rows:
        if not row.spec.emits_observations:
            continue
        comparisons = (
            ("financial-year", row.values[2], row.values[3], row.values[4]),
            ("year-on-year", row.values[1], row.values[3], row.values[5]),
        )
        for name, comparison, current, reported in comparisons:
            if comparison is None or current is None or reported is None or comparison == 0:
                skipped += 1
                continue
            implied = ((current / comparison) - Decimal(1)) * Decimal(100)
            checks += 1
            if abs(implied - reported) > ROUNDING_TOLERANCE:
                raise DataValidationError(
                    f"Growth reconciliation failed for {row.spec.source_label!r} {name}: "
                    f"reported={reported}, implied={implied}, tolerance={ROUNDING_TOLERANCE}"
                )
    return checks, skipped


def _series_id(spec: _RowSpec, measure: str) -> str:
    namespace = "RBI.SECTION42" if spec.population_id == POPULATION_ALL else "RBI.SIBC"
    return f"{namespace}.{spec.base_series_id}.{measure}"


def _observation(
    *,
    source: _SourceTable,
    row: _ParsedRow,
    measure: str,
    observation_date: str,
    comparison_date: str | None,
    value: Decimal | None,
    unit: str,
) -> dict[str, Any]:
    spec = row.spec
    return {
        "dataset_id": DATASET_ID,
        "series_id": _series_id(spec, measure),
        "source_table": source.title,
        "source_row_code": spec.source_row_code,
        "source_label": spec.source_label,
        "parent_row_code": spec.parent_row_code,
        "sector_level_1": spec.sector_level_1,
        "sector_level_2": spec.sector_level_2,
        "sector_level_3": spec.sector_level_3,
        "measure": measure,
        "observation_date": observation_date,
        "comparison_date": comparison_date,
        "value": value,
        "unit": unit,
        "population_id": spec.population_id,
        "publication_date": source.publication_date,
        "bulletin_period": source.bulletin_period,
        "is_provisional": True,
        "is_memorandum": spec.is_memorandum,
        "footnote_references": json.dumps(list(spec.footnotes), separators=(",", ":")),
        "source_url": source.source_url,
        "source_sha256": source.source_sha256,
        "parser_version": PARSER_VERSION,
        "layout_id": LAYOUT_ID,
    }


def _rows_to_observations(
    source: _SourceTable,
    rows: Iterable[_ParsedRow],
    *,
    section42_override_date: str,
    skip_source_row_code: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    observations: list[dict[str, Any]] = []
    emitted_rows = 0
    for row in rows:
        if not row.spec.emits_observations or row.spec.source_row_code == skip_source_row_code:
            continue
        emitted_rows += 1
        column_2_date = source.header_dates.column_2_date
        if source.title == MAJOR_TITLE and row.spec.population_id == POPULATION_ALL:
            column_2_date = section42_override_date
        values = row.values
        for date, value in (
            (column_2_date, values[1]),
            (source.header_dates.column_1_date, values[0]),
            (source.header_dates.column_4_date, values[3]),
        ):
            observations.append(
                _observation(
                    source=source,
                    row=row,
                    measure=MEASURE_OUTSTANDING,
                    observation_date=date,
                    comparison_date=None,
                    value=value,
                    unit=UNIT_INR_CRORE,
                )
            )
        observations.append(
            _observation(
                source=source,
                row=row,
                measure=MEASURE_FY_GROWTH,
                observation_date=source.header_dates.column_4_date,
                comparison_date=source.header_dates.column_3_date,
                value=values[4],
                unit=UNIT_PERCENT,
            )
        )
        observations.append(
            _observation(
                source=source,
                row=row,
                measure=MEASURE_YOY_GROWTH,
                observation_date=source.header_dates.column_4_date,
                comparison_date=column_2_date,
                value=values[5],
                unit=UNIT_PERCENT,
            )
        )
    return observations, emitted_rows


def _validate_canonical_keys(observations: pd.DataFrame) -> int:
    key = ["dataset_id", "series_id", "observation_date", "publication_date"]
    duplicate_mask = observations.duplicated(key, keep=False)
    count = int(duplicate_mask.sum())
    if count:
        duplicates = observations.loc[duplicate_mask, key].to_dict("records")
        raise DataValidationError(f"Duplicate canonical observation keys: {duplicates!r}")
    return count


def _render_csv_value(value: Any, *, missing_value: str) -> str:
    if value is None or (not isinstance(value, (str, Decimal)) and pd.isna(value)):
        return missing_value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _observations_csv_bytes(
    observations: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    sort_rows: bool,
    missing_value: str,
) -> bytes:
    missing = [column for column in columns if column not in observations.columns]
    if missing:
        raise DataValidationError(f"Observation columns are missing required fields: {missing}")
    rows = [
        tuple(_render_csv_value(value, missing_value=missing_value) for value in row)
        for row in observations.loc[:, columns].itertuples(index=False, name=None)
    ]
    if sort_rows:
        rows.sort()
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def observations_to_csv_bytes(observations: pd.DataFrame) -> bytes:
    """Serialize the complete provenance-bearing canonical observations."""
    if tuple(observations.columns) != OBSERVATION_COLUMNS:
        raise DataValidationError("Observation columns do not match the canonical schema")
    return _observations_csv_bytes(
        observations,
        OBSERVATION_COLUMNS,
        sort_rows=False,
        missing_value="",
    )


def semantic_observations_to_csv_bytes(observations: pd.DataFrame) -> bytes:
    """Serialize economic observations independently of row order and provenance."""
    return _observations_csv_bytes(
        observations,
        SEMANTIC_OBSERVATION_COLUMNS,
        sort_rows=True,
        missing_value=SEMANTIC_MISSING_VALUE,
    )


def semantic_observations_sha256(observations: pd.DataFrame) -> str:
    """Return the stable economic-observation identity."""
    return hashlib.sha256(semantic_observations_to_csv_bytes(observations)).hexdigest()


def provenance_bound_output_sha256(observations: pd.DataFrame) -> str:
    """Return the identity of complete canonical output including provenance."""
    return hashlib.sha256(observations_to_csv_bytes(observations)).hexdigest()


def _layout_signature() -> str:
    def row_signature(spec: _RowSpec) -> dict[str, Any]:
        return {
            "source_label": spec.source_label,
            "source_row_code": spec.source_row_code,
            "base_series_id": spec.base_series_id,
            "parent_row_code": spec.parent_row_code,
            "hierarchy": [spec.sector_level_1, spec.sector_level_2, spec.sector_level_3],
            "population_id": spec.population_id,
            "is_memorandum": spec.is_memorandum,
            "footnotes": list(spec.footnotes),
            "emits_observations": spec.emits_observations,
        }

    semantic_layout = {
        "layout_id": LAYOUT_ID,
        "titles": [MAJOR_TITLE, INDUSTRY_TITLE],
        "unit": UNIT_MARKER,
        "column_roles": [
            "OUTSTANDING_DUPLICATE_HEADLINE",
            "OUTSTANDING_PRIOR_YEAR",
            "OUTSTANDING_DUPLICATE_GROWTH_BASE",
            "OUTSTANDING_CURRENT",
            MEASURE_FY_GROWTH,
            MEASURE_YOY_GROWTH,
        ],
        "major_rows": [row_signature(spec) for spec in MAJOR_ROW_SPECS],
        "industry_rows": [row_signature(spec) for spec in INDUSTRY_ROW_SPECS],
        "required_population_markers": [
            "Section-42 return",
            "covers all scheduled commercial banks (SCBs)",
            "sector-wise and industry-wise bank credit (SIBC) return",
            "covers select banks accounting for about 95 per cent",
        ],
    }
    payload = json.dumps(semantic_layout, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _prepare_input(value: bytes | str, *, name: str) -> tuple[bytes, str, str]:
    if isinstance(value, str):
        raw = value.encode("utf-8")
        text = value
    elif isinstance(value, bytes):
        raw = value
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedLayoutError(f"{name} is not valid UTF-8") from exc
    else:
        raise TypeError(f"{name} must be bytes or str")
    if len(raw) > MAX_INPUT_BYTES:
        raise DataValidationError(f"{name} exceeds the {MAX_INPUT_BYTES}-byte parser bound")
    return raw, text, hashlib.sha256(raw).hexdigest()


def parse_sectoral_credit_bulletin(
    major_sectors_html: bytes | str,
    industries_html: bytes | str,
    *,
    major_sectors_url: str,
    industries_url: str,
) -> ParsedSectoralCredit:
    """Parse the supported RBI Bulletin tables from supplied HTML only."""
    major_raw, major_text, major_hash = _prepare_input(
        major_sectors_html, name="major_sectors_html"
    )
    industry_raw, industry_text, industry_hash = _prepare_input(
        industries_html, name="industries_html"
    )
    del major_raw, industry_raw

    major = _extract_source_table(
        major_text,
        title=MAJOR_TITLE,
        source_url=major_sectors_url,
        source_sha256=major_hash,
    )
    industry = _extract_source_table(
        industry_text,
        title=INDUSTRY_TITLE,
        source_url=industries_url,
        source_sha256=industry_hash,
    )
    if major.publication_date != industry.publication_date:
        raise DataValidationError("The two source tables have different publication dates")
    if major.bulletin_period != industry.bulletin_period:
        raise DataValidationError("The two source tables have different Bulletin periods")
    if major.header_dates != industry.header_dates:
        raise DataValidationError("The two source tables have different column/date headers")

    section42_override = _validate_required_notes(major, industry)
    major_rows, major_duplicate_columns = _map_rows(major, MAJOR_ROW_SPECS)
    industry_rows, industry_duplicate_columns = _map_rows(industry, INDUSTRY_ROW_SPECS)

    major_industry = next(row for row in major_rows if row.spec.source_row_code == "2")
    industry_total = next(row for row in industry_rows if row.spec.source_row_code == "2")
    if (
        major_industry.spec.base_series_id != industry_total.spec.base_series_id
        or major_industry.values != industry_total.values
    ):
        raise DataValidationError(
            "Cross-table duplicate Industry row disagrees between Tables 15 and 16"
        )

    growth_checks, growth_skipped = _reconcile_growth((*major_rows, *industry_rows))
    major_observations, major_emitted = _rows_to_observations(
        major, major_rows, section42_override_date=section42_override
    )
    industry_observations, industry_emitted = _rows_to_observations(
        industry,
        industry_rows,
        section42_override_date=section42_override,
        skip_source_row_code="2",
    )
    observations = pd.DataFrame(
        [*major_observations, *industry_observations], columns=OBSERVATION_COLUMNS
    )
    canonical_duplicate_count = _validate_canonical_keys(observations)
    semantic_hash = semantic_observations_sha256(observations)
    provenance_hash = provenance_bound_output_sha256(observations)

    measure_counts = tuple(
        (measure, int((observations["measure"] == measure).sum()))
        for measure in (MEASURE_OUTSTANDING, MEASURE_FY_GROWTH, MEASURE_YOY_GROWTH)
    )
    population_counts = tuple(
        (population, int((observations["population_id"] == population).sum()))
        for population in (POPULATION_ALL, POPULATION_SELECT)
    )
    metadata = ParseMetadata(
        dataset_id=DATASET_ID,
        layout_id=LAYOUT_ID,
        parser_version=PARSER_VERSION,
        source_hashes=((MAJOR_TITLE, major_hash), (INDUSTRY_TITLE, industry_hash)),
        publication_date=major.publication_date,
        bulletin_period=major.bulletin_period,
        current_observation_date=major.header_dates.column_4_date,
        data_row_counts=((MAJOR_TITLE, len(major.raw_rows)), (INDUSTRY_TITLE, len(industry.raw_rows))),
        mapped_row_counts=((MAJOR_TITLE, len(major_rows)), (INDUSTRY_TITLE, len(industry_rows))),
        emitted_row_counts=((MAJOR_TITLE, major_emitted), (INDUSTRY_TITLE, industry_emitted)),
        observation_counts_by_measure=measure_counts,
        observation_counts_by_population=population_counts,
        unknown_row_count=0,
        duplicate_source_column_count=major_duplicate_columns + industry_duplicate_columns,
        duplicate_source_row_count=1,
        duplicate_source_rows=((MAJOR_TITLE, "2", INDUSTRY_TITLE, "2"),),
        canonical_duplicate_key_count=canonical_duplicate_count,
        growth_reconciliation_checks=growth_checks,
        growth_reconciliation_skipped=growth_skipped,
        growth_reconciliation_failures=0,
        growth_reconciliation_tolerance_percentage_points=str(ROUNDING_TOLERANCE),
        layout_signature_sha256=_layout_signature(),
        semantic_observations_sha256=semantic_hash,
        provenance_bound_output_sha256=provenance_hash,
    )
    notes = (SourceNote(MAJOR_TITLE, major.note), SourceNote(INDUSTRY_TITLE, industry.note))
    return ParsedSectoralCredit(observations=observations, metadata=metadata, notes=notes)


__all__ = [
    "AmbiguousTableError",
    "DataValidationError",
    "ParseMetadata",
    "ParsedSectoralCredit",
    "SectoralCreditParseError",
    "SourceNote",
    "SEMANTIC_OBSERVATION_COLUMNS",
    "UnmappedSeriesError",
    "UnsupportedLayoutError",
    "observations_to_csv_bytes",
    "parse_sectoral_credit_bulletin",
    "provenance_bound_output_sha256",
    "semantic_observations_sha256",
    "semantic_observations_to_csv_bytes",
]
