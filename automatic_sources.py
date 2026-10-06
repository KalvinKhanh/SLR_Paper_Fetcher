"""Find public full-text copies by DOI, without interacting with challenges."""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup

from config import BASE_DIR, settings
from http_policy import polite_get


def known_public_copy(doi):
    try:
        records = json.loads((BASE_DIR / "official_pdf_sources.json").read_text(encoding="utf-8"))
        return records.get(doi.lower(), {})
    except (OSError, ValueError):
        return {}


def check_openalex(doi):
    settings.refresh_api_credentials()
    headers = {"Accept": "application/json"}
    if settings.OPENALEX_API_KEY:
        headers["Authorization"] = "Bearer " + settings.OPENALEX_API_KEY
    try:
        with polite_get(
            "https://api.openalex.org/works/" + quote("https://doi.org/" + doi, safe=""),
            headers=headers, timeout=8,
        ) as response:
            if response.status_code != 200:
                return {}
            data = response.json()
        if (data.get("doi") or "").removeprefix("https://doi.org/").lower() != doi.lower():
            return {}
        locations = [data.get("best_oa_location") or {}] + (data.get("locations") or [])
        urls = list(dict.fromkeys(
            location["pdf_url"] for location in locations
            if location.get("is_oa") and location.get("pdf_url")
        ))
        return {"urls": urls, "title": data.get("title") or "", "source": "OpenAlex / kho toàn văn công khai"}
    except (requests.RequestException, ValueError, AttributeError, TypeError):
        return {}


def check_pmc(doi):
    """Use PMC's official identifier/OA APIs, including embargo checks."""
    try:
        with polite_get("https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/",
                        params={"ids": doi, "idtype": "doi", "format": "json", "tool": "SLRPaperFetcher", "email": settings.UNPAYWALL_EMAIL},
                        timeout=8) as response:
            if response.status_code != 200:
                return {}
            records = response.json().get("records", [])
        record = next((record for record in records if str(record.get("doi", "")).lower() == doi.lower()), {})
        pmcid = record.get("pmcid", "")
        if not re.fullmatch(r"PMC\d+", pmcid) or str(record.get("live", "true")).lower() == "false":
            return {}
        with polite_get("https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi", params={"id": pmcid}, timeout=8) as response:
            if response.status_code != 200:
                return {}
            document = BeautifulSoup(response.content, "xml")
        urls = []
        for link in document.select('record link[format="pdf"]'):
            url = link.get("href", "")
            if urlparse(url).hostname == "ftp.ncbi.nlm.nih.gov":
                urls.append(url.replace("ftp://", "https://", 1))
        return {"urls": urls, "source": "PMC Open Access", "landing_url": "https://pmc.ncbi.nlm.nih.gov/articles/" + pmcid + "/"}
    except (requests.RequestException, ValueError, AttributeError, TypeError):
        return {}


def download_public_copy(doi, title, output_path, progress_callback=None, status_callback=None):
    # Imported here so the browser engine can call this module without a cycle.
    from downloader_engine import check_arxiv, check_unpaywall, check_semantic_scholar, download_file_direct

    attempted = set()
    failures = []

    def try_source(info):
        urls = info.get("urls") or ([info["url"]] if info.get("url") else [])
        for url in urls[:6]:
            if url in attempted or urlparse(url).scheme not in ("https", "http"):
                continue
            attempted.add(url)
            success, message = download_file_direct(
                url, Path(output_path),
                (lambda percent, received, total: progress_callback(percent)) if progress_callback else None,
            )
            if success:
                return True, f"Đã tải PDF tự động từ {info.get('source') or 'nguồn toàn văn công khai'}."
            failures.append((urlparse(url).hostname or "nguồn PDF") + ": " + message)
        return False, ""

    if status_callback:
        status_callback("checking_public_sources")
    known = known_public_copy(doi)
    if known:
        success, message = try_source(known)
        if success:
            return success, message
        title = title or known.get("title")

    # One request per provider at a time per article, maximum three independent
    # providers. The application's download lanes bound concurrent articles.
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(provider, doi) for provider in (check_unpaywall, check_semantic_scholar, check_openalex, check_pmc)]
        candidates = []
        for future in as_completed(futures):
            try:
                info = future.result()
            except Exception:
                continue
            title = title or info.get("title")
            if info.get("urls") or (info.get("has_pdf") and info.get("url")):
                candidates.append(info)
    for info in candidates:
        success, message = try_source(info)
        if success:
            return success, message

    if status_callback:
        status_callback("checking_arxiv")
    info = check_arxiv(doi, title or None)
    if info.get("has_pdf"):
        success, message = try_source(info)
        if success:
            return success, message
    from paper_io import safe_message
    details = " Phản hồi nguồn: " + "; ".join(failures[-3:]) if failures else ""
    return False, safe_message("Chưa tìm được PDF tải được từ các nguồn toàn văn công khai." + details)
