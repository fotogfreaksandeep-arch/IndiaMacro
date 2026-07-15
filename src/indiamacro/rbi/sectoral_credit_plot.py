"""Optional, public-API plotting helpers for RBI sectoral-credit history."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final, Sequence


INR_CRORE: Final = "INR_CRORE"
PERCENT: Final = "PERCENT"
LAKH_CRORE_DIVISOR: Final = Decimal("100000")
LAST_REPORTING_FRIDAY: Final = "LAST_REPORTING_FRIDAY"
CALENDAR_MONTH_END: Final = "CALENDAR_MONTH_END"
FULLY_COMPARABLE: Final = "FULLY_COMPARABLE"
DEFAULT_TITLE: Final = "RBI Non-food Credit: Outstanding and Reported YoY Growth"
PLOT_EXTRA_MESSAGE: Final = (
    'Plotting RBI sectoral-credit history requires Matplotlib. Install it with '
    'pip install "indiamacro[plot]".'
)


class SectoralCreditPlotError(RuntimeError):
    """Base class for sectoral-credit plotting failures."""


class PlottingDependencyError(SectoralCreditPlotError, ImportError):
    """The optional plotting dependency is unavailable."""


class ChartAlignmentError(SectoralCreditPlotError, ValueError):
    """Selected outstanding and growth publication points do not align."""


class ChartCompatibilityError(SectoralCreditPlotError, ValueError):
    """Selected series use incompatible measures, units, or populations."""


@dataclass(frozen=True)
class SectoralCreditChartPoint:
    observation_date: str
    publication_date: str
    outstanding_inr_crore: Decimal
    outstanding_inr_lakh_crore: Decimal
    reported_yoy_growth_percent: Decimal
    population_id: str
    outstanding_unit: str
    growth_unit: str
    methodology_regime: str
    comparability_classification: str
    outstanding_source_url: str
    growth_source_url: str
    outstanding_source_sha256: str
    growth_source_sha256: str
    bulletin_period: str


def _load_matplotlib() -> tuple[Any, Any, Any]:
    try:
        matplotlib = importlib.import_module("matplotlib")
        dates = importlib.import_module("matplotlib.dates")
        pyplot = importlib.import_module("matplotlib.pyplot")
    except (ImportError, ModuleNotFoundError) as exc:
        raise PlottingDependencyError(PLOT_EXTRA_MESSAGE) from exc
    matplotlib.rcParams["svg.hashsalt"] = "indiamacro-sectoral-credit-demo-v1"
    return matplotlib, dates, pyplot


def _publication_issue(item: Any) -> str:
    try:
        return datetime.strptime(item.bulletin_period, "%B %Y").strftime("%Y-%m")
    except (AttributeError, TypeError, ValueError) as exc:
        raise ChartCompatibilityError("Selected observations have an invalid Bulletin period") from exc


def _validate_measure(items: Sequence[Any], expected: str, label: str) -> None:
    measures = {item.measure for item in items}
    if measures != {expected}:
        raise ChartCompatibilityError(
            f"{label} series must contain only {expected}; found {sorted(measures)}"
        )


def _index_points(items: Sequence[Any], label: str) -> dict[tuple[str, str], Any]:
    indexed: dict[tuple[str, str], Any] = {}
    for item in items:
        key = (item.observation_date, item.publication_date)
        if key in indexed:
            raise ChartAlignmentError(f"{label} series contains duplicate publication points")
        indexed[key] = item
    return indexed


def _relevant_boundary(history: Any, issues: set[str]) -> Any | None:
    boundaries = tuple(history.metadata.methodology_boundaries)
    relevant = [
        boundary
        for boundary in boundaries
        if boundary.from_issue in issues and boundary.to_issue in issues
    ]
    if len(relevant) > 1:
        raise ChartCompatibilityError("Selected range contains multiple methodology boundaries")
    return relevant[0] if relevant else None


def sectoral_credit_history_chart_data(
    history: Any,
    *,
    outstanding_series_id: str,
    growth_series_id: str,
) -> tuple[SectoralCreditChartPoint, ...]:
    """Join two current-release series using exact observation and publication dates."""
    outstanding = tuple(history.select(series_id=outstanding_series_id, view="current"))
    growth = tuple(history.select(series_id=growth_series_id, view="current"))
    if not outstanding or not growth:
        raise ChartAlignmentError("Both selected current series must contain observations")
    _validate_measure(outstanding, "OUTSTANDING", "Outstanding")
    _validate_measure(growth, "YOY_GROWTH_REPORTED", "Growth")
    outstanding_units = {item.unit for item in outstanding}
    growth_units = {item.unit for item in growth}
    if outstanding_units != {INR_CRORE}:
        raise ChartCompatibilityError(
            f"Outstanding series must use {INR_CRORE}; found {sorted(outstanding_units)}"
        )
    if growth_units != {PERCENT}:
        raise ChartCompatibilityError(
            f"Growth series must use {PERCENT}; found {sorted(growth_units)}"
        )
    outstanding_populations = {item.population_id for item in outstanding}
    growth_populations = {item.population_id for item in growth}
    if len(outstanding_populations) != 1 or outstanding_populations != growth_populations:
        raise ChartCompatibilityError(
            "Outstanding and growth series must use one identical population"
        )
    outstanding_by_key = _index_points(outstanding, "Outstanding")
    growth_by_key = _index_points(growth, "Growth")
    if outstanding_by_key.keys() != growth_by_key.keys():
        outstanding_only = sorted(outstanding_by_key.keys() - growth_by_key.keys())
        growth_only = sorted(growth_by_key.keys() - outstanding_by_key.keys())
        raise ChartAlignmentError(
            "Outstanding and growth point sets differ by observation/publication date: "
            f"outstanding_only={outstanding_only}, growth_only={growth_only}"
        )
    issues = {_publication_issue(item) for item in outstanding}
    boundary = _relevant_boundary(history, issues)
    population_id = next(iter(outstanding_populations))
    points = []
    for key in sorted(outstanding_by_key):
        outstanding_item = outstanding_by_key[key]
        growth_item = growth_by_key[key]
        if _publication_issue(outstanding_item) != _publication_issue(growth_item):
            raise ChartAlignmentError("Aligned points identify different Bulletin issues")
        if outstanding_item.value is None or growth_item.value is None:
            raise ChartCompatibilityError("Charted current observations may not be missing")
        issue = _publication_issue(outstanding_item)
        if boundary is None:
            regime = (
                LAST_REPORTING_FRIDAY
                if "LAST_FRIDAY" in outstanding_item.layout_id
                or "V2_2025H2" in outstanding_item.layout_id
                else CALENDAR_MONTH_END
            )
            comparability = FULLY_COMPARABLE
        elif issue <= boundary.from_issue:
            regime = LAST_REPORTING_FRIDAY
            comparability = FULLY_COMPARABLE
        else:
            regime = CALENDAR_MONTH_END
            comparability = (
                boundary.classification if issue == boundary.to_issue else FULLY_COMPARABLE
            )
        value = outstanding_item.value
        points.append(
            SectoralCreditChartPoint(
                observation_date=outstanding_item.observation_date,
                publication_date=outstanding_item.publication_date,
                outstanding_inr_crore=value,
                outstanding_inr_lakh_crore=value / LAKH_CRORE_DIVISOR,
                reported_yoy_growth_percent=growth_item.value,
                population_id=population_id,
                outstanding_unit=outstanding_item.unit,
                growth_unit=growth_item.unit,
                methodology_regime=regime,
                comparability_classification=comparability,
                outstanding_source_url=outstanding_item.source_url,
                growth_source_url=growth_item.source_url,
                outstanding_source_sha256=outstanding_item.source_sha256,
                growth_source_sha256=growth_item.source_sha256,
                bulletin_period=outstanding_item.bulletin_period,
            )
        )
    return tuple(points)


def _boundary_position(history: Any, points: Sequence[SectoralCreditChartPoint]) -> date | None:
    issues = {
        datetime.strptime(point.bulletin_period, "%B %Y").strftime("%Y-%m")
        for point in points
    }
    boundary = _relevant_boundary(history, issues)
    if boundary is None:
        return None
    dates_by_issue = {
        datetime.strptime(point.bulletin_period, "%B %Y").strftime("%Y-%m"): date.fromisoformat(
            point.observation_date
        )
        for point in points
    }
    before = dates_by_issue[boundary.from_issue]
    after = dates_by_issue[boundary.to_issue]
    return before + timedelta(days=(after - before).days / 2)


def plot_sectoral_credit_history(
    history: Any,
    *,
    outstanding_series_id: str,
    growth_series_id: str,
    title: str | None = None,
    annotate_methodology: bool = True,
) -> tuple[Any, tuple[Any, Any]]:
    """Plot exact current-release outstanding and RBI-reported YoY observations."""
    points = sectoral_credit_history_chart_data(
        history,
        outstanding_series_id=outstanding_series_id,
        growth_series_id=growth_series_id,
    )
    matplotlib, mdates, pyplot = _load_matplotlib()
    style = {
        "axes.edgecolor": "#4D4D4D",
        "axes.labelcolor": "#222222",
        "axes.titleweight": "bold",
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "savefig.dpi": 160,
        "svg.hashsalt": "indiamacro-sectoral-credit-demo-v1",
        "text.color": "#222222",
        "xtick.color": "#333333",
        "ytick.color": "#333333",
    }
    dates = [date.fromisoformat(point.observation_date) for point in points]
    outstanding = [float(point.outstanding_inr_lakh_crore) for point in points]
    growth = [float(point.reported_yoy_growth_percent) for point in points]
    with matplotlib.rc_context(style):
        figure, axes_value = pyplot.subplots(
            2,
            1,
            sharex=True,
            figsize=(10, 7),
            dpi=120,
            gridspec_kw={"height_ratios": (1.35, 1)},
        )
        top, bottom = axes_value
        top.plot(dates, outstanding, color="#0072B2", marker="o", linewidth=2, markersize=5)
        bottom.plot(dates, growth, color="#D55E00", marker="o", linewidth=2, markersize=5)
        top.set_title("Non-food Credit outstanding", loc="left", fontsize=11)
        bottom.set_title("RBI-reported year-on-year growth", loc="left", fontsize=11)
        top.set_ylabel("INR lakh crore\n(1 lakh crore = 100,000 crore)")
        bottom.set_ylabel("Per cent")
        bottom.axhline(0, color="#777777", linewidth=0.8, alpha=0.65, zorder=0)
        for axis in (top, bottom):
            axis.grid(axis="y", color="#D9D9D9", linewidth=0.7, alpha=0.7)
            axis.spines[["top", "right"]].set_visible(False)
            axis.margins(x=0.025)
        bottom.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
        bottom.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
        boundary_date = _boundary_position(history, points) if annotate_methodology else None
        if boundary_date is not None:
            for axis in (top, bottom):
                axis.axvline(
                    boundary_date,
                    color="#666666",
                    linestyle=(0, (3, 3)),
                    linewidth=1,
                    alpha=0.8,
                )
            top.annotate(
                "Reporting-date basis changes\nlast Friday → month-end",
                xy=(boundary_date, 1),
                xycoords=("data", "axes fraction"),
                xytext=(7, -8),
                textcoords="offset points",
                ha="left",
                va="top",
                fontsize=8.5,
                color="#444444",
            )
        figure.suptitle(title or DEFAULT_TITLE, x=0.08, y=0.96, ha="left", fontsize=15)
        selected_start = history.metadata.selected_start_issue
        selected_end = history.metadata.selected_end_issue
        coverage = date.fromisoformat(points[-1].observation_date).strftime("%d %b %Y")
        footer = (
            "Source: Reserve Bank of India Bulletin, Current Statistics Tables 15 and 16. "
            f"Bulletin issues {selected_start}–{selected_end}; observations through {coverage}.\n"
            "Current-release observations; no interpolation. Prior-year comparison dates retain "
            "RBI's documented convention."
        )
        figure.text(0.08, 0.035, footer, ha="left", va="bottom", fontsize=8, color="#555555")
        figure.subplots_adjust(left=0.11, right=0.97, top=0.88, bottom=0.19, hspace=0.2)
    return figure, (top, bottom)


__all__ = [
    "ChartAlignmentError",
    "ChartCompatibilityError",
    "PlottingDependencyError",
    "SectoralCreditChartPoint",
    "SectoralCreditPlotError",
    "plot_sectoral_credit_history",
    "sectoral_credit_history_chart_data",
]
