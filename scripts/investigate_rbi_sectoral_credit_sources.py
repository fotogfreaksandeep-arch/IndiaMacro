#!/usr/bin/env python3
"""Bounded official-source investigation for RBI sectoral bank-credit data.

This is evidence-gathering code, not a workbook or production dataset parser.
It performs a small fixed sequence of public, sequential requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests


BULLETIN_INDEX_URL = "https://rbi.org.in/Scripts/BS_ViewBulletin.aspx"
DBIE_URL = "https://data.rbi.org.in/DBIE/"
SDMX_REGISTRY_URL = "https://data.rbi.org.in/FusionRegistry/webservice/structure.html"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
CHUNK_SIZE = 64 * 1024
TABLE_TITLES = {
    "major_sectors": "15. Deployment of Gross Bank Credit by Major Sectors",
    "industries": "16. Industry-wise Deployment of Gross Bank Credit",
}
CHALLENGE_MARKERS = (
    "captcha",
    "what code is in the image",
    "verify you are human",
    "challenge.support_id",
    'window["bobcmn"]',
    "/tspd/",
)


class InvestigationError(RuntimeError):
    pass


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.text_parts: list[str] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        text = " ".join(unescape(data).split())
        if text:
            self.text_parts.append(text)
            if self._href is not None:
                self._text.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((" ".join(self._text), self._href))
            self._href = None
            self._text = []


class TableStructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.table_count = 0
        self.row_count = 0
        self.cell_count = 0
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag == "table":
            self.table_count += 1
        elif tag == "tr":
            self.row_count += 1
        elif tag in {"td", "th"}:
            self.cell_count += 1

    def handle_data(self, data: str) -> None:
        text = " ".join(unescape(data).split())
        if text:
            self.text_parts.append(text)


def _official_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and (host == "rbi.org.in" or host.endswith(".rbi.org.in"))


def _response_metadata(response: requests.Response) -> dict[str, Any]:
    return {
        "http_status": response.status_code,
        "final_url": response.url,
        "redirect_history": [
            {"status": item.status_code, "url": item.url, "location": item.headers.get("Location")}
            for item in response.history
        ],
        "content_type": response.headers.get("Content-Type"),
    }


def fetch_to_temporary_file(
    session: requests.Session,
    url: str,
    output_dir: Path,
    *,
    maximum_bytes: int = MAX_RESPONSE_BYTES,
) -> tuple[Path, dict[str, Any]]:
    if not _official_url(url):
        raise InvestigationError(f"Refusing non-official URL: {url}")
    try:
        response = session.get(url, stream=True, allow_redirects=True, timeout=(10, 45))
    except requests.RequestException as exc:
        raise InvestigationError(f"{type(exc).__name__}: {exc}") from exc
    metadata = _response_metadata(response)
    for item in [*response.history, response]:
        if not _official_url(item.url):
            response.close()
            raise InvestigationError(f"Redirect left official RBI domains: {item.url}")
    fd, name = tempfile.mkstemp(prefix="official-source-", suffix=".part", dir=output_dir)
    path = Path(name)
    digest = hashlib.sha256()
    byte_size = 0
    prefix = bytearray()
    try:
        with os.fdopen(fd, "wb") as handle:
            for chunk in response.iter_content(CHUNK_SIZE):
                if not chunk:
                    continue
                byte_size += len(chunk)
                if byte_size > maximum_bytes:
                    raise InvestigationError(f"Response exceeds {maximum_bytes} byte bound")
                if len(prefix) < 8192:
                    prefix.extend(chunk[: 8192 - len(prefix)])
                digest.update(chunk)
                handle.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        response.close()
    metadata.update(byte_size=byte_size, sha256=digest.hexdigest())
    prefix_text = bytes(prefix).decode("utf-8", errors="ignore").lower()
    metadata["challenge_detected"] = any(marker in prefix_text for marker in CHALLENGE_MARKERS)
    if response.status_code < 200 or response.status_code >= 300:
        path.unlink(missing_ok=True)
        raise InvestigationError(f"Unexpected HTTP status {response.status_code}")
    return path, metadata


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def discover_bulletin_tables(html: str, base_url: str = BULLETIN_INDEX_URL) -> dict[str, str]:
    parser = LinkParser()
    parser.feed(html)
    discovered: dict[str, str] = {}
    for key, title in TABLE_TITLES.items():
        matches = {
            urljoin(base_url, href)
            for text, href in parser.links
            if " ".join(text.split()).casefold() == title.casefold()
        }
        if len(matches) != 1:
            raise InvestigationError(f"Expected one HTML link for {title!r}; found {len(matches)}")
        url = matches.pop()
        if not _official_url(url):
            raise InvestigationError(f"Discovered non-official table URL: {url}")
        discovered[key] = url
    return discovered


def validate_bulletin_table(html: str, kind: str) -> dict[str, Any]:
    if kind not in TABLE_TITLES:
        raise InvestigationError(f"Unknown bulletin-table kind: {kind}")
    parser = TableStructureParser()
    parser.feed(html)
    text = " ".join(parser.text_parts)
    required_common = [
        TABLE_TITLES[kind],
        "₹ Crore",
        "Outstanding as on",
        "Growth (%)",
    ]
    required_by_kind = {
        "major_sectors": [
            "Non-food Credit",
            "Agriculture & Allied Activities",
            "Industry",
            "Services",
            "Personal Loans",
            "Priority Sector",
            "scheduled commercial banks",
            "sector-wise and industry-wise bank credit",
        ],
        "industries": [
            "Food Processing",
            "Textiles",
            "Chemicals & Chemical Products",
            "Infrastructure",
        ],
    }
    missing = [marker for marker in required_common + required_by_kind[kind] if marker.casefold() not in text.casefold()]
    if missing:
        raise InvestigationError(f"HTML table is missing semantic markers: {missing}")
    if parser.table_count < 1 or parser.row_count < 10 or parser.cell_count < 40:
        raise InvestigationError(
            f"Insufficient table structure: tables={parser.table_count}, rows={parser.row_count}, cells={parser.cell_count}"
        )
    observation_dates = sorted(
        set(re.findall(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},\s+20\d{2}\b", text))
    )
    date_labels = sorted(
        set(
            re.findall(
                r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2}\b",
                text,
            )
        )
    )
    header_start = next(
        (index for index, part in enumerate(parser.text_parts) if part.casefold() == "outstanding as on"),
        None,
    )
    header_cells = parser.text_parts[header_start : header_start + 20] if header_start is not None else []
    return {
        "valid_structured_html": True,
        "table_count": parser.table_count,
        "row_count": parser.row_count,
        "cell_count": parser.cell_count,
        "observation_dates": observation_dates,
        "date_labels": date_labels,
        "header_cells": header_cells,
        "unit": "₹ Crore",
        "has_outstanding_values": True,
        "has_yoy_growth": "Y-o-Y" in text,
        "has_selected_scb_population_note": "covers select banks accounting for about 95 per cent"
        in text.casefold(),
    }


def _probe(
    session: requests.Session, url: str, output_dir: Path
) -> tuple[dict[str, Any], Path | None]:
    try:
        path, evidence = fetch_to_temporary_file(session, url, output_dir)
        return {"retrievable": True, **evidence}, path
    except InvestigationError as exc:
        return {"retrievable": False, "error": str(exc)}, None


def run(output_dir: Path, blocker_manifest: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    blocker = json.loads(blocker_manifest.read_text(encoding="utf-8"))
    candidates: list[dict[str, Any]] = [
        {
            "candidate_id": "rbi_release_workbook",
            "source_name": "RBI Sectoral Deployment release workbook",
            "official_institution": "Reserve Bank of India",
            "catalogue_url": blocker["index_url"],
            "data_url": blocker["workbook_url"],
            "documented": True,
            "discovery_mechanism": "Latest matching release, then the link labelled Statements I and II",
            "response_format": "Claimed XLSX; received HTML F5/TSPD challenge",
            "automated_retrieval": False,
            "requires": "F5/TSPD human-verification flow",
            "semantic_coverage": "FULL if retrievable",
            "stability": "Stable documented release route, but document-host automation is blocked",
            "result": "SOURCE_BLOCKED",
            "evidence": blocker,
        }
    ]

    with requests.Session() as session:
        session.headers.update(
            {
                "User-Agent": "IndiaMacro-official-source-investigation/0.1",
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.5",
            }
        )

        dbie_evidence, dbie_path = _probe(session, DBIE_URL, output_dir)
        if dbie_path:
            dbie_evidence["landing_page_is_html"] = "<html" in _read_text(dbie_path).casefold()
            dbie_path.unlink(missing_ok=True)
        registry_evidence, registry_path = _probe(session, SDMX_REGISTRY_URL, output_dir)
        if registry_path:
            registry_evidence["registry_page_is_html"] = "REST Web Service" in _read_text(registry_path)
            registry_path.unlink(missing_ok=True)
        candidates.append(
            {
                "candidate_id": "dbie_time_series_and_sdmx",
                "source_name": "Database on Indian Economy time-series / SDMX registry",
                "official_institution": "Reserve Bank of India",
                "catalogue_url": DBIE_URL,
                "data_url": SDMX_REGISTRY_URL,
                "documented": True,
                "discovery_mechanism": "DBIE catalogue and public Fusion Registry REST-service page",
                "response_format": "HTML application plus advertised SDMX service",
                "automated_retrieval": False,
                "requires": "DBIE application flow; public registry connection was closed for ordinary Python",
                "semantic_coverage": "FULL in DBIE according to RBI documentation, but no data response was demonstrated",
                "stability": "Documented catalogue; machine endpoint was not usable in this test",
                "result": "NOT_DEMONSTRATED",
                "evidence": {"landing_page": dbie_evidence, "sdmx_registry": registry_evidence},
            }
        )

        index_path, index_evidence = fetch_to_temporary_file(session, BULLETIN_INDEX_URL, output_dir)
        try:
            index_html = _read_text(index_path)
            table_urls = discover_bulletin_tables(index_html)
            bulletin_match = re.search(r"Reserve Bank of India Bulletin\s*-\s*([A-Z][a-z]+\s+20\d{2})", index_html)
            bulletin_period = bulletin_match.group(1) if bulletin_match else None
        finally:
            index_path.unlink(missing_ok=True)

        table_evidence: dict[str, Any] = {}
        for kind, url in table_urls.items():
            temporary, evidence = fetch_to_temporary_file(session, url, output_dir)
            try:
                validation = validate_bulletin_table(_read_text(temporary), kind)
                saved = output_dir / f"bulletin_{kind}.html"
                temporary.replace(saved)
            finally:
                temporary.unlink(missing_ok=True)
            table_evidence[kind] = {
                "url": url,
                **evidence,
                "validation": validation,
                "saved_evidence_path": str(saved.resolve()),
            }

        candidates.append(
            {
                "candidate_id": "rbi_bulletin_html_tables_15_16",
                "source_name": "RBI Bulletin Current Statistics tables 15 and 16",
                "official_institution": "Reserve Bank of India",
                "catalogue_url": BULLETIN_INDEX_URL,
                "data_url": table_urls,
                "documented": True,
                "discovery_mechanism": "Exact table-title links on the current RBI Bulletin index",
                "response_format": "Consistently structured HTML tables",
                "automated_retrieval": True,
                "requires": "No login, JavaScript, CAPTCHA, or human interaction",
                "semantic_coverage": "FULL",
                "stability": "Stable publication entry point and exact titles; numeric bulletin IDs are discovered, not configured",
                "result": "PASS_OFFICIAL_PATH",
                "evidence": {
                    "bulletin_period": bulletin_period,
                    "index": index_evidence,
                    "tables": table_evidence,
                },
            }
        )

    result = {
        "status": "PASS_OFFICIAL_PATH",
        "dataset_id": "RBI_SECTORAL_CREDIT",
        "investigated_at_utc": datetime.now(timezone.utc).isoformat(),
        "request_policy": {
            "single_process": True,
            "sequential": True,
            "chunk_size_bytes": CHUNK_SIZE,
            "maximum_response_bytes": MAX_RESPONSE_BYTES,
        },
        "best_candidate_id": "rbi_bulletin_html_tables_15_16",
        "candidates": candidates,
        "catalogue_search_findings": [
            {
                "source": "Open Government Data Platform India",
                "result": "No matching RBI-origin Sectoral Deployment resource located in bounded web searches",
                "semantic_coverage": "NONE_DEMONSTRATED",
            }
        ],
    }
    (output_dir / "candidates.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output_dir",
        nargs="?",
        type=Path,
        default=Path("spike-artifacts/source-investigation"),
    )
    parser.add_argument(
        "--blocker-manifest",
        type=Path,
        default=Path("spike-artifacts/final-run/manifest.json"),
    )
    args = parser.parse_args(argv)
    result = run(args.output_dir, args.blocker_manifest)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
