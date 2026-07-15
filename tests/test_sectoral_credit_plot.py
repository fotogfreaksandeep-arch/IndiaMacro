from __future__ import annotations

import csv
import importlib
import importlib.util
import json
import subprocess
import sys
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from indiamacro import rbi


ROOT = Path(__file__).parents[1]
ACCEPTANCE_CACHE = ROOT / "spike-artifacts/history-connector-v1/acceptance-cache"
OUTSTANDING_ID = "RBI.SECTION42.NON_FOOD_CREDIT.OUTSTANDING"
GROWTH_ID = "RBI.SECTION42.NON_FOOD_CREDIT.YOY_GROWTH_REPORTED"
plot_module = importlib.import_module("indiamacro.rbi.sectoral_credit_plot")
history_module = importlib.import_module("indiamacro.rbi.sectoral_credit_history")
SCRIPT_PATH = ROOT / "scripts/plot_rbi_sectoral_credit_history.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location("sectoral_credit_demo", SCRIPT_PATH)
demo = importlib.util.module_from_spec(SCRIPT_SPEC)
assert SCRIPT_SPEC.loader is not None
sys.modules[SCRIPT_SPEC.name] = demo
SCRIPT_SPEC.loader.exec_module(demo)


@pytest.fixture(scope="session")
def history_result():
    if not ACCEPTANCE_CACHE.exists():
        pytest.skip("ignored verified history acceptance cache is unavailable")
    return rbi.sectoral_credit_history(offline=True, cache_dir=ACCEPTANCE_CACHE)


@pytest.fixture(scope="session")
def chart_points(history_result):
    return rbi.sectoral_credit_history_chart_data(
        history_result,
        outstanding_series_id=OUTSTANDING_ID,
        growth_series_id=GROWTH_ID,
    )


class FakeHistory:
    def __init__(self, base, outstanding, growth) -> None:
        self.metadata = base.metadata
        self.outstanding = tuple(outstanding)
        self.growth = tuple(growth)
        self.calls: list[tuple[str, str]] = []

    def select(self, *, series_id: str, view: str):
        self.calls.append((series_id, view))
        return self.outstanding if series_id == OUTSTANDING_ID else self.growth


def _selected(history_result):
    return (
        history_result.select(series_id=OUTSTANDING_ID, view="current"),
        history_result.select(series_id=GROWTH_ID, view="current"),
    )


def test_public_plotting_exports() -> None:
    assert callable(rbi.plot_sectoral_credit_history)
    assert callable(rbi.sectoral_credit_history_chart_data)
    assert issubclass(rbi.PlottingDependencyError, ImportError)


def test_base_import_succeeds_when_matplotlib_is_blocked() -> None:
    code = """
import builtins
real = builtins.__import__
def blocked(name, *args, **kwargs):
    if name.split('.')[0] == 'matplotlib':
        raise ImportError('blocked optional dependency')
    return real(name, *args, **kwargs)
builtins.__import__ = blocked
import indiamacro
from indiamacro import rbi
assert callable(rbi.sectoral_credit_history)
assert callable(rbi.plot_sectoral_credit_history)
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_missing_dependency_error_is_actionable(history_result, monkeypatch) -> None:
    original = plot_module.importlib.import_module

    def missing(name: str):
        if name.split(".")[0] == "matplotlib":
            raise ModuleNotFoundError("No module named matplotlib", name="matplotlib")
        return original(name)

    monkeypatch.setattr(plot_module.importlib, "import_module", missing)
    with pytest.raises(rbi.PlottingDependencyError, match=r'indiamacro\[plot\]'):
        rbi.plot_sectoral_credit_history(
            history_result,
            outstanding_series_id=OUTSTANDING_ID,
            growth_series_id=GROWTH_ID,
        )


def test_chart_data_uses_public_select_and_exact_join(history_result) -> None:
    outstanding, growth = _selected(history_result)
    fake = FakeHistory(history_result, outstanding, growth)
    points = rbi.sectoral_credit_history_chart_data(
        fake,
        outstanding_series_id=OUTSTANDING_ID,
        growth_series_id=GROWTH_ID,
    )
    assert fake.calls == [(OUTSTANDING_ID, "current"), (GROWTH_ID, "current")]
    assert len(points) == 12
    assert [(item.observation_date, item.publication_date) for item in points] == [
        (item.observation_date, item.publication_date) for item in outstanding
    ]


def test_exact_values_dates_interval_and_conversion(chart_points) -> None:
    assert chart_points[0].observation_date == "2025-05-30"
    assert chart_points[0].outstanding_inr_crore == Decimal("18217016")
    assert chart_points[-1].observation_date == "2026-04-30"
    assert chart_points[-1].outstanding_inr_crore == Decimal("21107913")
    assert chart_points[0].outstanding_inr_lakh_crore == Decimal("182.17016")
    intervals = [
        (date.fromisoformat(right.observation_date) - date.fromisoformat(left.observation_date)).days
        for left, right in zip(chart_points, chart_points[1:])
    ]
    assert 42 in intervals
    assert [item.observation_date for item in chart_points][6:9] == [
        "2025-11-28",
        "2025-12-31",
        "2026-01-31",
    ]


def test_reported_growth_is_used_without_recomputation(history_result) -> None:
    outstanding, growth = _selected(history_result)
    changed_growth = list(growth)
    changed_growth[0] = replace(changed_growth[0], value=Decimal("99.9"))
    points = rbi.sectoral_credit_history_chart_data(
        FakeHistory(history_result, outstanding, changed_growth),
        outstanding_series_id=OUTSTANDING_ID,
        growth_series_id=GROWTH_ID,
    )
    assert points[0].reported_yoy_growth_percent == Decimal("99.9")


@pytest.mark.parametrize("field", ["observation_date", "publication_date"])
def test_mismatched_point_sets_fail(history_result, field: str) -> None:
    outstanding, growth = _selected(history_result)
    changed = list(growth)
    changed[0] = replace(
        changed[0],
        **{field: "2025-05-29" if field == "observation_date" else "2025-07-22"},
    )
    with pytest.raises(rbi.ChartAlignmentError, match="point sets differ"):
        rbi.sectoral_credit_history_chart_data(
            FakeHistory(history_result, outstanding, changed),
            outstanding_series_id=OUTSTANDING_ID,
            growth_series_id=GROWTH_ID,
        )


def test_incompatible_population_fails(history_result) -> None:
    outstanding, growth = _selected(history_result)
    changed = [replace(item, population_id="OTHER_POPULATION") for item in growth]
    with pytest.raises(rbi.ChartCompatibilityError, match="identical population"):
        rbi.sectoral_credit_history_chart_data(
            FakeHistory(history_result, outstanding, changed),
            outstanding_series_id=OUTSTANDING_ID,
            growth_series_id=GROWTH_ID,
        )


@pytest.mark.parametrize(
    ("target", "unit", "match"),
    [("outstanding", "USD", "INR_CRORE"), ("growth", "RATIO", "PERCENT")],
)
def test_incompatible_units_fail(history_result, target: str, unit: str, match: str) -> None:
    outstanding, growth = _selected(history_result)
    if target == "outstanding":
        outstanding = tuple(replace(item, unit=unit) for item in outstanding)
    else:
        growth = tuple(replace(item, unit=unit) for item in growth)
    with pytest.raises(rbi.ChartCompatibilityError, match=match):
        rbi.sectoral_credit_history_chart_data(
            FakeHistory(history_result, outstanding, growth),
            outstanding_series_id=OUTSTANDING_ID,
            growth_series_id=GROWTH_ID,
        )


def test_methodology_classification_is_derived_from_metadata(chart_points) -> None:
    assert [item.methodology_regime for item in chart_points[:7]] == [
        "LAST_REPORTING_FRIDAY"
    ] * 7
    assert [item.methodology_regime for item in chart_points[7:]] == [
        "CALENDAR_MONTH_END"
    ] * 5
    assert chart_points[7].comparability_classification == (
        "COMPARABLE_WITH_DATE_BASIS_CHANGE"
    )


def test_chart_data_does_not_mutate_input(history_result) -> None:
    before_vintages = history_result.vintages
    before_current = history_result.current_observations
    rbi.sectoral_credit_history_chart_data(
        history_result,
        outstanding_series_id=OUTSTANDING_ID,
        growth_series_id=GROWTH_ID,
    )
    assert history_result.vintages is before_vintages
    assert history_result.current_observations is before_current


def test_plot_structure_values_annotation_and_source_note(history_result, chart_points) -> None:
    pytest.importorskip("matplotlib")
    figure, axes = rbi.plot_sectoral_credit_history(
        history_result,
        outstanding_series_id=OUTSTANDING_ID,
        growth_series_id=GROWTH_ID,
    )
    assert len(axes) == 2
    assert list(axes[0].lines[0].get_ydata()) == [
        float(item.outstanding_inr_lakh_crore) for item in chart_points
    ]
    assert list(axes[1].lines[0].get_ydata()) == [
        float(item.reported_yoy_growth_percent) for item in chart_points
    ]
    assert "1 lakh crore = 100,000 crore" in axes[0].get_ylabel()
    assert any("basis changes" in item.get_text() for item in axes[0].texts)
    figure_text = " ".join(item.get_text() for item in figure.texts)
    assert "Reserve Bank of India Bulletin" in figure_text
    assert "Prior-year comparison dates" in figure_text


def test_annotation_can_be_disabled(history_result) -> None:
    pytest.importorskip("matplotlib")
    _figure, axes = rbi.plot_sectoral_credit_history(
        history_result,
        outstanding_series_id=OUTSTANDING_ID,
        growth_series_id=GROWTH_ID,
        annotate_methodology=False,
    )
    assert not any("basis changes" in item.get_text() for item in axes[0].texts)


def test_deterministic_csv_and_manifest_semantics(chart_points, history_result) -> None:
    first = demo._csv_bytes(chart_points)
    second = demo._csv_bytes(chart_points)
    assert first == second
    rows = list(csv.DictReader(first.decode().splitlines()))
    assert tuple(rows[0]) == demo.CSV_COLUMNS
    assert [item["observation_date"] for item in rows] == sorted(
        item["observation_date"] for item in rows
    )
    manifest_one = demo._manifest(
        history_result,
        chart_points,
        csv_content=first,
        output_hashes={"rbi_non_food_credit_history.csv": demo._sha256(first)},
    )
    manifest_two = demo._manifest(
        history_result,
        chart_points,
        csv_content=second,
        output_hashes={"rbi_non_food_credit_history.csv": demo._sha256(second)},
    )
    assert manifest_one == manifest_two
    assert manifest_one["point_count"] == 12
    assert manifest_one["chart_data_sha256"]
    assert manifest_one["semantic_manifest_sha256"]


def test_demo_runs_offline_twice_with_identical_outputs(tmp_path, monkeypatch) -> None:
    pytest.importorskip("matplotlib")

    def forbidden():
        raise AssertionError("offline demo created a network session")

    monkeypatch.setattr(history_module, "_new_session", forbidden)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first = demo.generate_demo(
        cache_dir=ACCEPTANCE_CACHE,
        offline=True,
        output_dir=first_dir,
    )
    second = demo.generate_demo(
        cache_dir=ACCEPTANCE_CACHE,
        offline=True,
        output_dir=second_dir,
    )
    assert first == second
    filenames = sorted(path.name for path in first_dir.iterdir())
    assert filenames == [
        "rbi_non_food_credit_history.csv",
        "rbi_non_food_credit_history.png",
        "rbi_non_food_credit_history.svg",
        "rbi_non_food_credit_history_manifest.json",
    ]
    for filename in filenames:
        assert (first_dir / filename).read_bytes() == (second_dir / filename).read_bytes()
    combined = b"".join((first_dir / filename).read_bytes() for filename in filenames)
    assert str(tmp_path).encode() not in combined
    assert b"/Users/" not in combined
    assert b"acceptance-cache" not in combined
    manifest = json.loads((first_dir / filenames[-1]).read_text())
    assert manifest["point_count"] == 12
    assert manifest["outstanding_series_id"] == OUTSTANDING_ID
    assert manifest["growth_series_id"] == GROWTH_ID
