"""Resolve publisher hosts from DOI redirects; never infer them from prefixes."""

from urllib.parse import quote, urlparse
import requests

from paper_io import normalize_doi, provider_for_url, public_url
from paper_store import get_store
from http_policy import polite_get


def resolve_doi(value, force=False):
    doi = normalize_doi(value)
    if not doi:
        return {"status": "INVALID_DOI", "message": "DOI không hợp lệ."}
    store = get_store()
    cached = store.get(doi)
    if not force and cached and cached.get("resolved_url"):
        return {key: cached[key] for key in ("resolved_url", "hostname", "provider")}
    try:
        # Stream and close: resolution must not download publisher HTML/PDF bodies.
        with polite_get("https://doi.org/" + quote(doi, safe="/"),
                          headers={"Accept": "text/html", "User-Agent": "SLR-Paper-Fetcher/1.0"},
                          timeout=(8, 12), allow_redirects=True, stream=True) as response:
            url = public_url(response.url)
            host = urlparse(url).hostname or ""
            if not host or host in ("doi.org", "dx.doi.org"):
                status = "INVALID_DOI" if response.status_code == 404 else "RESOLUTION_FAILED"
                store.update(doi, status, message=f"DOI resolver trả HTTP {response.status_code}.")
                return {"status": status, "message": f"Không resolve được DOI (HTTP {response.status_code})."}
            # A publisher's HTTP 403 still identifies the resolved provider.
            result = {"resolved_url": url, "hostname": host, "provider": provider_for_url(url)}
            store.update(doi, (cached or {}).get("status", "PENDING"), **result)
            return result
    except requests.RequestException as error:
        store.update(doi, "RESOLUTION_FAILED", message=f"DOI resolver: {type(error).__name__}")
        return {"status": "RESOLUTION_FAILED", "message": "Chưa resolve được DOI do kết nối; vẫn thử nguồn công khai."}
