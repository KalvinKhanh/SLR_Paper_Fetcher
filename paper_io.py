"""DOI normalization, safe file names, and PDF validation shared by all routes."""

import re
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse, parse_qsl, urlencode, quote

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[^\s<>\x00-\x1f]+", re.IGNORECASE)


def normalize_doi(value):
    value = str(value or "").strip().strip('<>"')
    parsed = urlparse(value)
    if parsed.scheme in ("http", "https"):
        if (parsed.hostname or "").lower() not in ("doi.org", "dx.doi.org", "www.doi.org"):
            return ""
        value = unquote(parsed.path.lstrip("/"))
    else:
        value = re.sub(r"^doi\s*:\s*", "", value, flags=re.I)
    return value.lower() if DOI_PATTERN.fullmatch(value) else ""


def safe_filename(name, suffix=".pdf"):
    name = str(name or "").strip()
    if not name or name in (".", "..") or re.search(r'[\\/:\x00-\x1f]', name):
        raise ValueError("Tên file không được chứa đường dẫn hoặc ký tự điều khiển.")
    name = re.sub(r'[<>"|?*]', "_", name).rstrip(". ")
    if not name.lower().endswith(suffix):
        name += suffix
    if re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", name, re.I):
        name = "_" + name
    if len(name.encode("utf-8")) > 220:
        raise ValueError("Tên file quá dài.")
    return name


def download_path(folder, name, suffix=".pdf"):
    root = Path(folder).resolve()
    path = (root / safe_filename(name, suffix)).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Đường dẫn nằm ngoài thư mục tải.")
    return path


def valid_pdf_file(path):
    try:
        path = Path(path)
        size = path.stat().st_size
        if size < 32:
            return False
        with path.open("rb") as stream:
            header = stream.read(1024)
            stream.seek(max(0, size - 4096))
            tail = stream.read()
        return b"%PDF-" in header and b"%%EOF" in tail and not header.lstrip().startswith(b"<")
    except OSError:
        return False


def provider_for_url(url):
    host = (urlparse(url).hostname or "").lower()
    domains = {
        "Elsevier": ("sciencedirect.com", "elsevier.com", "sciencedirectassets.com"),
        "IEEE": ("ieeexplore.ieee.org",),
        "Springer": ("springer.com", "springernature.com", "nature.com"),
        "Wiley": ("wiley.com",),
        "TaylorFrancis": ("tandfonline.com",),
        "Sage": ("sagepub.com",),
        "arXiv": ("arxiv.org",),
        "PMC": ("pmc.ncbi.nlm.nih.gov",),
    }
    for provider, suffixes in domains.items():
        if any(host == domain or host.endswith("." + domain) for domain in suffixes):
            return provider
    return "Generic"


def public_url(url):
    """Remove query credentials and fragments from stored/logged navigation URLs."""
    try:
        parsed = urlparse(str(url or ""))
        port = parsed.port
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    hostname = "[" + parsed.hostname + "]" if ":" in parsed.hostname else parsed.hostname
    host = hostname + (f":{port}" if port else "")
    identifiers = {"doi", "id", "arnumber", "pii", "uri", "article_id", "articlenumber", "documentid"}
    query = urlencode([(key, value) for key, value in parse_qsl(parsed.query) if key.lower() in identifiers])
    return urlunparse((parsed.scheme, host, parsed.path, "", query, ""))


def institutional_url(base, doi):
    target = "https://doi.org/" + quote(doi, safe="/")
    return base + quote(target, safe="")


def safe_message(message):
    return re.sub(r"https?://[^\s'\"<>]+", lambda match: public_url(match.group()), str(message))
