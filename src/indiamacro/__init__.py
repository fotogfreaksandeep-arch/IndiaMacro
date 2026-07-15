"""IndiaMacro public package."""

from importlib.metadata import PackageNotFoundError, version

from indiamacro import rbi as rbi

from indiamacro.rbi.sectoral_credit_bulletin import (
    AmbiguousTableError,
    DataValidationError,
    ParseMetadata,
    ParsedSectoralCredit,
    SectoralCreditParseError,
    SourceNote,
    UnmappedSeriesError,
    UnsupportedLayoutError,
    observations_to_csv_bytes,
    parse_sectoral_credit_bulletin,
)

try:
    __version__ = version("indiamacro")
except PackageNotFoundError:
    __version__ = "0+unknown"

__all__ = [
    "AmbiguousTableError",
    "DataValidationError",
    "ParseMetadata",
    "ParsedSectoralCredit",
    "SectoralCreditParseError",
    "SourceNote",
    "UnmappedSeriesError",
    "UnsupportedLayoutError",
    "__version__",
    "observations_to_csv_bytes",
    "parse_sectoral_credit_bulletin",
    "rbi",
]
