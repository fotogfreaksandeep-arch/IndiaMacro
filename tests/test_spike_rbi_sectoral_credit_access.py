import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest
import requests


SCRIPT = Path(__file__).parents[1] / "scripts" / "spike_rbi_sectoral_credit_access.py"
SPEC = importlib.util.spec_from_file_location("rbi_spike", SCRIPT)
spike = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = spike
SPEC.loader.exec_module(spike)


def test_discovers_latest_release_by_month_not_page_position():
    html = """
      <a href='/Scripts/BS_PressReleaseDisplay.aspx?prid=2'>
        Sectoral Deployment of Bank Credit – April 2026</a>
      <span>Apr 30, 2026</span>
      <span>Jun 30, 2026</span>
      <a href='/Scripts/BS_PressReleaseDisplay.aspx?prid=3'>
        Sectoral Deployment of Bank Credit – May 2026</a>
      <a href='/Scripts/BS_PressReleaseDisplay.aspx?prid=1'>
        Sectoral Deployment of Bank Credit – December 2025</a>
    """
    release = spike.discover_latest_release(html)
    assert release.title.endswith("May 2026")
    assert release.url == "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=3"
    assert release.release_date.isoformat() == "2026-06-30"


def test_workbook_requires_statements_label_and_resolves_relative_url():
    html = """
      <a href='https://rbidocs.rbi.org.in/first.xlsx'>unrelated spreadsheet</a>
      <p>Data are set out in <a href='//rbidocs.rbi.org.in/rdocs/content.xlsx'>Statements I and II</a>.</p>
    """
    assert spike.discover_workbook_url(html, spike.INDEX_URL) == "https://rbidocs.rbi.org.in/rdocs/content.xlsx"


def test_rejects_non_rbi_workbook_host():
    html = "<a href='https://example.com/file.xlsx'>Statements I and II</a>"
    with pytest.raises(spike.SpikeError, match="non-official"):
        spike.discover_workbook_url(html, spike.INDEX_URL)


def test_html_with_successful_http_semantics_cannot_validate_as_xlsx(tmp_path):
    response_file = tmp_path / "response.part"
    response_file.write_bytes(b"<!doctype html><html><title>200 OK but blocked</title></html>")
    with pytest.raises(spike.SpikeError, match="is HTML"):
        spike.validate_xlsx_response(response_file)


def test_http_200_tspd_challenge_is_source_blocked():
    response = requests.Response()
    response.status_code = 200
    response.url = "https://rbidocs.rbi.org.in/rdocs/content/docs/file.xlsx"
    with pytest.raises(spike.SpikeError) as caught:
        spike._check_response_access(
            response,
            b'<!doctype html><script>window["bobcmn"]="challenge";</script>',
            "download_workbook",
        )
    assert caught.value.status == "SOURCE_BLOCKED"


def test_minimal_structural_xlsx_validates(tmp_path):
    workbook = tmp_path / "valid.part"
    with zipfile.ZipFile(workbook, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    spike.validate_xlsx_response(workbook)
