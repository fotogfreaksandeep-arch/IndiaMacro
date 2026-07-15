import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "investigate_rbi_sectoral_credit_sources.py"
SPEC = importlib.util.spec_from_file_location("source_investigation", SCRIPT)
investigation = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = investigation
SPEC.loader.exec_module(investigation)


def test_discovers_exact_bulletin_table_links():
    html = """
    <a href='/Scripts/BS_ViewBulletin.aspx?Id=100'>15. Deployment of Gross Bank Credit by Major Sectors</a>
    <a href='/Scripts/BS_ViewBulletin.aspx?Id=101'>16. Industry-wise Deployment of Gross Bank Credit</a>
    """
    assert investigation.discover_bulletin_tables(html) == {
        "major_sectors": "https://rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=100",
        "industries": "https://rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=101",
    }


def test_rejects_ambiguous_bulletin_table_link():
    html = """
    <a href='?Id=100'>15. Deployment of Gross Bank Credit by Major Sectors</a>
    <a href='?Id=102'>15. Deployment of Gross Bank Credit by Major Sectors</a>
    <a href='?Id=101'>16. Industry-wise Deployment of Gross Bank Credit</a>
    """
    with pytest.raises(investigation.InvestigationError, match="Expected one HTML link"):
        investigation.discover_bulletin_tables(html)


@pytest.mark.parametrize(
    ("kind", "specific_markers"),
    [
        (
            "major_sectors",
            "Non-food Credit Agriculture & Allied Activities Industry Services Personal Loans Priority Sector "
            "sector-wise and industry-wise bank credit covers select banks accounting for about 95 per cent "
            "of total non-food credit extended by all scheduled commercial banks",
        ),
        (
            "industries",
            "Food Processing Textiles Chemicals & Chemical Products Infrastructure",
        ),
    ],
)
def test_validates_semantically_structured_html(kind, specific_markers):
    header = (
        f"<tr><th>{investigation.TABLE_TITLES[kind]}</th><th>₹ Crore</th>"
        "<th>Outstanding as on</th><th>Growth (%)</th><th>Apr. 30, 2026</th><th>Y-o-Y</th></tr>"
    )
    rows = "".join(f"<tr><td>{i}</td><td>100</td><td>1.0</td><td>x</td></tr>" for i in range(12))
    html = f"<html><table>{header}<tr><th>{specific_markers}</th></tr>{rows}</table></html>"
    result = investigation.validate_bulletin_table(html, kind)
    assert result["valid_structured_html"] is True
    assert result["unit"] == "₹ Crore"
    assert result["has_yoy_growth"] is True
    assert result["header_cells"][0] == "Outstanding as on"
