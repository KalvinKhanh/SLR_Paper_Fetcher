import os
import re
import time
import json
from urllib.parse import urlparse
from difflib import SequenceMatcher
from threading import Lock
from contextlib import ExitStack
import requests
import cloudscraper
import asyncio
from pathlib import Path
from bs4 import BeautifulSoup
from typing import Optional, Tuple, Dict, Any
from config import settings
from browser_pdf import BrowserPdfCapture, is_complete_pdf
from paper_io import normalize_doi, download_path, valid_pdf_file, safe_message, public_url, institutional_url, provider_for_url
from paper_store import get_store
from browser_session import browser_worker
from institutional_auth import is_authenticated, has_fulltext_access, requires_mfa, trusted_login_page, visible
from http_policy import polite_get
from browser_verification import detect_cloudflare_challenge, wait_for_browser_verification, verification_state, is_sciencedirect_article_page

HEADERS = {
    "User-Agent": "SLR-Paper-Fetcher/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
}

def extract_doi(text: str) -> str:
    """Trích xuất mã DOI chuẩn từ URL hoặc chuỗi văn bản."""
    return normalize_doi(text)

def check_unpaywall(doi: str) -> dict:
    """Kiểm tra nguồn mở Open Access chính thống qua Unpaywall."""
    url = f"https://api.unpaywall.org/v2/{doi}?email={settings.UNPAYWALL_EMAIL}"
    try:
        r = polite_get(url, headers=HEADERS, timeout=7)
        if r.status_code == 200:
            data = r.json()
            is_oa = data.get("is_oa", False)
            title = data.get("title", "")
            if is_oa:
                best = data.get("best_oa_location") or {}
                pdf_url = best.get("url_for_pdf")
                landing_url = best.get("url_for_landing_page") or best.get("url")

                if not pdf_url:
                    for loc in data.get("oa_locations", []):
                        if loc.get("url_for_pdf"):
                            pdf_url = loc.get("url_for_pdf")
                            if not landing_url:
                                landing_url = loc.get("url_for_landing_page") or loc.get("url")
                            break

                if pdf_url:
                    return {
                        "found": True,
                        "has_pdf": True,
                        "url": pdf_url,
                        "landing_url": landing_url,
                        "title": title,
                        "source": "Open Access (Unpaywall)"
                    }

                return {
                    "found": True,
                    "has_pdf": False,
                    "url": None,
                    "landing_url": landing_url or f"https://doi.org/{doi}",
                    "title": title,
                    "source": "Open Access (Web)"
                }
    except Exception:
        pass
    return {"found": False, "has_pdf": False, "url": None, "landing_url": None, "title": None, "source": None}

def check_semantic_scholar(doi: str) -> dict:
    """Kiểm tra nguồn mở Open Access bổ sung qua Semantic Scholar API."""
    url = f"https://api.semanticscholar.org/graph/v1/paper/{doi}?fields=title,openAccessPdf"
    try:
        r = polite_get(url, headers=HEADERS, timeout=6)
        if r.status_code == 200:
            data = r.json()
            title = data.get("title", "")
            oa_data = data.get("openAccessPdf") or {}
            pdf_url = oa_data.get("url")
            
            # Kiểm tra xem đây có phải link PDF thật hay chỉ là landing page web (như doi.org / sciencedirect)
            is_real_pdf = False
            if pdf_url and pdf_url.startswith("http"):
                url_lower = pdf_url.lower()
                if "doi.org" in url_lower and not url_lower.endswith(".pdf"):
                    is_real_pdf = False
                elif "sciencedirect.com" in url_lower and "/pdf" not in url_lower and not url_lower.endswith(".pdf"):
                    is_real_pdf = False
                else:
                    is_real_pdf = True

            if is_real_pdf:
                return {
                    "found": True,
                    "has_pdf": True,
                    "url": pdf_url,
                    "title": title,
                    "source": "Open Access (Semantic Scholar)"
                }
            if title:
                return {
                    "found": True,
                    "has_pdf": False,
                    "url": None,
                    "title": title,
                    "source": "Semantic Scholar"
                }
    except Exception:
        pass
    return {"found": False, "has_pdf": False, "url": None, "title": None, "source": None}

_arxiv_api_lock = Lock()
_arxiv_last_request = 0.0


def check_arxiv(doi: str, title: Optional[str] = None) -> dict:
    """Find a matching openly available arXiv version using its public API."""
    global _arxiv_last_request
    api_url = "https://export.arxiv.org/api/query"
    query = f'all:"{doi}"'
    if title:
        safe_title = re.sub(r'[(){}"]', " ", title[:200]).strip()
        if safe_title:
            query += f' OR ti:"{safe_title}"'

    normalized_doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi.strip(), flags=re.I).rstrip(" .").lower()
    normalize_title = lambda value: re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()

    try:
        with _arxiv_api_lock:
            delay = 3.2 - (time.monotonic() - _arxiv_last_request)
            if delay > 0:
                time.sleep(delay)
            response = polite_get(
                api_url,
                params={"search_query": query, "start": 0, "max_results": 10},
                headers=HEADERS,
                timeout=10,
            )
            _arxiv_last_request = time.monotonic()
        if response.status_code != 200:
            return {"found": False, "has_pdf": False, "url": None, "landing_url": None,
                    "title": None, "source": None, "error": f"arXiv API trả HTTP {response.status_code}"}
        feed = BeautifulSoup(response.content, "xml")
        for entry in feed.find_all("entry"):
            doi_element = entry.find("arxiv:doi")
            entry_doi = doi_element.get_text(" ", strip=True).lower() if doi_element else ""
            title_element = entry.find("title")
            entry_title = re.sub(r"\s+", " ", title_element.get_text(" ", strip=True)) if title_element else ""
            exact_doi = entry_doi == normalized_doi
            title_match = bool(title and normalize_title(title) and
                               normalize_title(title) == normalize_title(entry_title))
            if entry_doi and not exact_doi:
                continue
            if not exact_doi and not title_match:
                continue
            id_element = entry.find("id")
            abstract_url = id_element.get_text(strip=True) if id_element else ""
            id_match = re.search(r"arxiv\.org/abs/(.+)$", abstract_url, re.I)
            if not id_match:
                continue
            arxiv_id = id_match.group(1).split("?")[0]
            return {
                "found": True, "has_pdf": True,
                "url": f"https://arxiv.org/pdf/{arxiv_id}",
                "landing_url": f"https://arxiv.org/abs/{arxiv_id}",
                "title": entry_title, "source": "Open Access (arXiv; bản preprint)"
            }
    except Exception:
        return {"found": False, "has_pdf": False, "url": None, "landing_url": None,
                "title": None, "source": None, "error": "Không kết nối được arXiv API"}
    return {"found": False, "has_pdf": False, "url": None, "landing_url": None,
            "title": None, "source": None, "error": "Không tìm thấy bản khớp DOI/tiêu đề trên arXiv"}

def find_paper_fulltext(doi: str) -> Dict[str, Any]:
    """
    Quy trình tích hợp đa tầng để tìm link tải PDF trực tiếp:
    1. Kiểm tra Unpaywall (Gold / Green OA)
    2. Kiểm tra Semantic Scholar OA
    3. Bản OA từ arXiv được tra trong luồng tải tự động, trước bước VNU
    """
    from automatic_sources import known_public_copy
    known = known_public_copy(doi)
    if known.get("url"):
        return {"found": True, "has_pdf": True, "pdf_url": known["url"],
                "landing_url": known["url"], "title": known.get("title"),
                "source": known.get("source"), "status": "Open Access"}

    # 1. Unpaywall
    unpaywall_info = check_unpaywall(doi)
    title = unpaywall_info.get("title")

    if unpaywall_info.get("has_pdf"):
        return {
            "found": True,
            "has_pdf": True,
            "pdf_url": unpaywall_info.get("url"),
            "landing_url": unpaywall_info.get("landing_url"),
            "title": title,
            "source": unpaywall_info.get("source"),
            "status": "Open Access"
        }

    # 2. Semantic Scholar
    ss_info = check_semantic_scholar(doi)
    if not title and ss_info.get("title"):
        title = ss_info.get("title")

    if ss_info.get("has_pdf"):
        return {
            "found": True,
            "has_pdf": True,
            "pdf_url": ss_info.get("url"),
            "landing_url": unpaywall_info.get("landing_url"),
            "title": title,
            "source": ss_info.get("source"),
            "status": "Open Access"
        }

    # 4. Nếu là Open Access nhưng chỉ có trang web
    if unpaywall_info.get("found"):
        return {
            "found": True,
            "has_pdf": False,
            "pdf_url": None,
            "landing_url": unpaywall_info.get("landing_url"),
            "title": title,
            "source": unpaywall_info.get("source"),
            "status": "Open Access (Web)"
        }

    # 5. Hoàn toàn Paywalled (cần dùng Proxy VNU)
    return {
        "found": False,
        "has_pdf": False,
        "pdf_url": None,
        "landing_url": f"https://doi.org/{doi}",
        "title": title,
        "source": None,
        "status": "Paywalled"
    }

def download_file_direct(url: str, output_path: Path, progress_callback=None) -> Tuple[bool, str]:
    """Download a PDF and report byte progress when the server provides a size."""
    if not isinstance(url, str) or urlparse(url).scheme not in ("http", "https") or not urlparse(url).hostname:
        return False, "URL download khong hop le."

    output_path.parent.mkdir(parents=True, exist_ok=True)
    custom_headers = dict(HEADERS)
    if "ieee.org" in url:
        custom_headers["Referer"] = "https://ieeexplore.ieee.org/"

    partial_path = output_path.with_suffix(output_path.suffix + ".part")
    response = None
    scraper = None
    try:
        scraper = cloudscraper.create_scraper()
        custom_headers["User-Agent"] = scraper.headers.get("User-Agent", custom_headers["User-Agent"])
        response = polite_get(url, session=scraper, headers=custom_headers, timeout=25, allow_redirects=True, stream=True)
        if response.status_code in (401, 403, 429):
            return False, f"Nguồn PDF trả HTTP {response.status_code}; đã chuyển nguồn."
        if response.status_code == 200:
            if "text/html" in response.headers.get("content-type", "").lower():
                return False, "INVALID_PDF: Nguồn trả về HTML thay vì PDF."
            total_bytes = int(response.headers.get("content-length") or 0)
            received_bytes = 0
            last_reported_percent = -1
            last_reported_mb = -1
            with open(partial_path, "wb") as output_file:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    output_file.write(chunk)
                    received_bytes += len(chunk)
                    if progress_callback:
                        percent = min(99, int(received_bytes * 100 / total_bytes)) if total_bytes else None
                        current_mb = received_bytes // (1024 * 1024)
                        if (percent is not None and percent != last_reported_percent) or (percent is None and current_mb != last_reported_mb):
                            progress_callback(percent, received_bytes, total_bytes)
                            last_reported_percent = percent if percent is not None else last_reported_percent
                            last_reported_mb = current_mb
            with open(partial_path, "rb") as downloaded_file:
                header = downloaded_file.read(5)
                downloaded_file.seek(max(0, received_bytes - 4096))
                tail = downloaded_file.read()
            is_pdf = valid_pdf_file(partial_path)
            if total_bytes and not response.headers.get("content-encoding") and received_bytes != total_bytes:
                is_pdf = False
            if is_pdf:
                partial_path.replace(output_path)
                if progress_callback:
                    progress_callback(100, received_bytes, total_bytes)
                return True, "Download complete."
            partial_path.unlink(missing_ok=True)
    except Exception as exc:
        print(f"PDF download warning for {urlparse(url).hostname}: {type(exc).__name__}")
        partial_path.unlink(missing_ok=True)
        if isinstance(exc, requests.HTTPError):
            return False, "Nguồn đang tạm nghỉ theo Retry-After; đã chuyển nguồn."
    finally:
        if response is not None:
            response.close()
        if scraper is not None:
            scraper.close()

    try:
        import subprocess
        temp_out = output_path.with_suffix(".tmp")
        cmd = [
            "curl.exe", "-L", "-s", "--fail", "-o", str(temp_out),
            "-A", custom_headers["User-Agent"],
            "-H", "Accept: application/pdf,text/html,*/*",
            "-e", custom_headers.get("Referer", ""), url
        ]
        subprocess.run(cmd, capture_output=True, timeout=30)
        if temp_out.exists() and temp_out.stat().st_size > 1000:
            with open(temp_out, "rb") as downloaded_file:
                header = downloaded_file.read(5)
                downloaded_file.seek(max(0, temp_out.stat().st_size - 4096))
                tail = downloaded_file.read()
            if valid_pdf_file(temp_out):
                temp_out.replace(output_path)
                if progress_callback:
                    progress_callback(100, output_path.stat().st_size, output_path.stat().st_size)
                return True, "Download complete (curl)."
        if temp_out.exists():
            temp_out.unlink()
    except Exception as exc:
        print(f"PDF download warning for {urlparse(url).hostname}: {type(exc).__name__}")
    finally:
        if 'temp_out' in locals():
            temp_out.unlink(missing_ok=True)

    return False, "Automatic download failed."

def accept_browser_cookies(page):
    """Accept visible cookie banners, including consent dialogs in iframes."""
    consent_name = re.compile(
        r"^(accept all(?: cookies)?|allow all(?: cookies)?|agree and (?:proceed|continue)|"
        r"accept cookies|chấp nhận tất cả(?: cookie)?|đồng ý tất cả)\s*$",
        re.IGNORECASE,
    )
    try:
        if page.is_closed():
            return False
        for frame in page.frames:
            candidates = (
                frame.get_by_role("button", name=consent_name),
                frame.locator(
                    '#onetrust-accept-btn-handler, #ccc-notify-accept, '
                    '#ccc-recommended-settings, #CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll'
                ),
            )
            for buttons in candidates:
                for index in range(min(buttons.count(), 5)):
                    button = buttons.nth(index)
                    if button.is_visible():
                        try:
                            button.click(timeout=2500)
                            button.wait_for(state="hidden", timeout=1500)
                            return True
                        except Exception:
                            # A successful click can navigate/detach its frame.
                            continue
    except Exception:
        pass
    return False


has_browser_verification = detect_cloudflare_challenge


class _VerificationPaused(BaseException):
    """Control flow: broad publisher error handlers must not swallow a pause."""
    def __init__(self, page):
        self.page = page


_VNU_STATE_LOCK = Lock()
_PUBLISHER_COOLDOWN_LOCK = Lock()
_publisher_cooldowns = {}


def publisher_is_cooling_down(resolution):
    with _PUBLISHER_COOLDOWN_LOCK:
        until = max(_publisher_cooldowns.get(resolution.get("hostname", ""), 0),
                    _publisher_cooldowns.get("provider:" + (resolution.get("provider") or "Generic"), 0))
        return time.monotonic() < until


def _run_sync_vnu(doi: str, output_filename: str, progress_callback=None, status_callback=None) -> Tuple[bool, str]:
    """Hàm đồng bộ chạy Playwright trong luồng độc lập, tránh xung đột asyncio loop trên Windows."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "Playwright chưa được cài đặt hoàn tất."

    # Queued papers may have been submitted before the preceding paper hit a
    # challenge. Recheck here on the owning thread, immediately before opening.
    if browser_worker.stopping.is_set():
        return False, "CANCELLED: Ứng dụng đang đóng; đã giữ profile đăng nhập."
    if browser_worker.challenge_session and (not browser_worker.browser or not browser_worker.browser.is_connected()):
        if status_callback:
            status_callback("waiting_for_verification")
        return False, "CAPTCHA_REQUIRED: Phiên xác minh đã đóng. Cần chủ động mở lại ứng dụng; không tự tạo browser/context mới."
    resolution = get_store().get(doi) or {}
    pending_page = browser_worker.pending_verifications.get(doi)
    pending_publisher = browser_worker.pending_publisher_page(resolution.get("provider"))
    if any(page.is_closed() for page in browser_worker.pending_verifications.values()):
        if status_callback:
            status_callback("waiting_for_verification")
        return False, "CAPTCHA_REQUIRED: Tab xác minh đã đóng. Cần chủ động mở lại ứng dụng để tiếp tục."
    if pending_publisher is not None and pending_page is None:
        if status_callback:
            status_callback("waiting_for_verification")
        return False, "CAPTCHA_REQUIRED: Một tab của nhà xuất bản đang chờ xác minh; giữ nguyên cửa sổ đó."
    if pending_page is None and publisher_is_cooling_down(resolution):
        if status_callback:
            status_callback("publisher_cooldown")
        return False, "CAPTCHA_REQUIRED: Nhà xuất bản đang tạm nghỉ sau xác minh; đang chuyển nguồn công khai."

    output_path = download_path(settings.DOWNLOAD_FOLDER, output_filename)
    vnu_url = institutional_url(settings.VNU_OPENATHENS_BASE_URL, doi)
    current_state = "IDLE"
    context = None
    pdf_capture = None
    submitted_forms = set()

    def save_browser_download(download):
        return pdf_capture.save_download(download)

    def wait_for_stable_page(page, timeout_ms=15000):
        deadline = time.monotonic() + timeout_ms / 1000
        last_url = None
        stable_checks = 0
        while time.monotonic() < deadline:
            if browser_worker.stopping.is_set():
                return False
            try:
                if page.is_closed():
                    return False
                if BrowserPdfCapture._is_pdf_url(page.url):
                    # The browser's built-in PDF viewer has no publisher DOM to
                    # wait for. The capture handles network/file completion.
                    return True
                page.wait_for_load_state("domcontentloaded", timeout=2500)
                ready_state = page.evaluate("document.readyState")
                current_url = page.url
                if ready_state in ("interactive", "complete") and current_url == last_url:
                    stable_checks += 1
                    if stable_checks >= 2:
                        return True
                else:
                    stable_checks = 0
                last_url = current_url
            except Exception:
                try:
                    if page.is_closed():
                        return False
                except Exception:
                    return False
                stable_checks = 0
            time.sleep(0.3)
        return False

    def close_browser_safely(browser):
        # The worker owns the browser. Save consent/login and detach this paper's
        # listeners; session() closes its tabs while retaining cookies/context.
        try:
            browser_worker.save_state()
        except Exception:
            pass

    def active_auth_page(current):
        pages = [target for target in reversed(browser_worker.article_pages()) if target not in pdf_capture.existing_pages]
        # A completed SSO popup can leave an old login form behind. An actual
        # article with full-text access (or its PDF) takes precedence over it.
        for candidate in pages:
            if not trusted_login_page(candidate) and (has_fulltext_access(candidate) or BrowserPdfCapture._is_pdf_url(candidate.url)):
                return candidate
        for candidate in pages:
            if trusted_login_page(candidate) and (visible(candidate, 'input[type="password"]') or requires_mfa(candidate)):
                return candidate
        for candidate in pages:
            if provider_for_url(candidate.url) == "Elsevier" and visible(candidate,
                    'input[aria-label*="organization" i], input[placeholder*="organization" i], input[name*="institution" i]'):
                return candidate
        for candidate in pages:
            if is_authenticated(candidate) or has_fulltext_access(candidate) or BrowserPdfCapture._is_pdf_url(candidate.url):
                return candidate
        return pages[0] if pages else current

    def select_sciencedirect_institution(page):
        """Follow Elsevier's organization SSO prompt for the configured VNU account."""
        try:
            guard_action(page)
            accept_browser_cookies(page)
            if is_authenticated(page) or has_fulltext_access(page):
                return False
            host = (urlparse(page.url).hostname or "").lower()
            if not any(host == domain or host.endswith("." + domain)
                       for domain in ("sciencedirect.com", "elsevier.com")):
                return False

            access_button = page.locator(
                'button:has-text("Access through your organization"), '
                'a:has-text("Access through your organization"), '
                '[aria-label*="Access through your organization" i], '
                'button:has-text("Access through"), '
                'a:has-text("Access through")'
            ).first
            if access_button.count() and access_button.is_visible():
                print("[AUTH] Clicking 'Access through your organization' button")
                guard_action(page)
                access_button.click(force=True, timeout=5000)
                wait_for_stable_page(page, timeout_ms=10000)
                page = active_auth_page(page)
                accept_browser_cookies(page)

            organization_input = page.locator(
                'input[aria-label*="organization" i], input[placeholder*="organization" i], '
                'input[name*="organization" i], input[id*="organization" i], '
                'input[name*="institution" i], input[id*="institution" i]'
            ).first
            if not organization_input.count():
                organization_input = page.get_by_label(
                    re.compile(r"Organization name or email", re.IGNORECASE)
                ).first
            if not organization_input.count():
                organization_input = page.locator('input[type="text"], input:not([type])').first

            if organization_input.count() and organization_input.is_visible():
                guard_action(page)
                organization_input.fill("Vietnam National University Ho Chi Minh City")
                page.wait_for_timeout(1500)

                submit_button = page.locator(
                    'button:has-text("Submit and continue"), input[type="submit"], '
                    'a:has-text("Submit and continue"), button:has-text("Continue"), button:has-text("Find")'
                ).first

                def find_and_select_target_org():
                    options_locator = page.locator(
                        '[role="option"], ul[role="listbox"] li, ul.results-list li, '
                        'ul li button, ul li a, button:has-text("Vietnam National University"), '
                        'a:has-text("Vietnam National University"), li:has-text("Vietnam National University"), '
                        'div:has-text("Vietnam National University")'
                    )
                    count = options_locator.count()
                    candidates = []
                    for idx in range(count):
                        try:
                            elem = options_locator.nth(idx)
                            if not elem.is_visible():
                                continue
                            text = (elem.inner_text() or "").strip()
                            if "vietnam national university" in text.lower() or "ho chi minh" in text.lower():
                                candidates.append((idx, elem, text))
                        except Exception:
                            continue

                    if not candidates:
                        return None

                    # Tiêu chí: "chọn cái thứ hai á ko phải cái law"
                    non_law_candidates = [c for c in candidates if "law" not in c[2].lower() and "luật" not in c[2].lower()]
                    chosen = None

                    if len(candidates) >= 2:
                        second_candidate = candidates[1]
                        if "law" not in second_candidate[2].lower() and "luật" not in second_candidate[2].lower():
                            chosen = second_candidate
                    
                    if not chosen and non_law_candidates:
                        chosen = non_law_candidates[0]

                    return chosen

                chosen_org = find_and_select_target_org()
                if not chosen_org and submit_button.count() and submit_button.is_visible():
                    guard_action(page)
                    submit_button.click(force=True, timeout=5000)
                    page.wait_for_timeout(2000)
                    chosen_org = find_and_select_target_org()

                if chosen_org:
                    idx, elem, text = chosen_org
                    print(f"[AUTH] Selected target institution #{idx+1} (non-law): {text}")
                    guard_action(page)
                    elem.click(force=True, timeout=5000)
                    wait_for_stable_page(page, timeout_ms=5000)

                    post_submit = page.locator(
                        'button:has-text("Submit and continue"), a:has-text("Submit and continue"), '
                        'button:has-text("Continue"), input[type="submit"]'
                    ).first
                    if post_submit.count() and post_submit.is_visible():
                        guard_action(page)
                        post_submit.click(force=True, timeout=5000)
                    wait_for_stable_page(page, timeout_ms=20000)
                    return True
                else:
                    fallback = page.get_by_text(
                        re.compile(r"^Vietnam National University Ho Chi Minh City$", re.I)
                    ).first
                    if fallback.count() and fallback.is_visible():
                        guard_action(page)
                        fallback.click(force=True, timeout=5000)
                        wait_for_stable_page(page, timeout_ms=10000)
                        return True
            return False
        except Exception as e:
            print(f"[AUTH] Error in select_sciencedirect_institution: {e}")
            return False

    def authenticate_vnu_redirect(page):
        for _ in range(2):
            try:
                guard_action(page)
                auth_host = (urlparse(page.url).hostname or "").lower()
                if not any(auth_host == domain or auth_host.endswith("." + domain)
                           for domain in ("openathens.net", "vnu.edu.vn", "vnuhcm.edu.vn")):
                    return
                accept_browser_cookies(page)
                if requires_mfa(page):
                    if status_callback:
                        status_callback("waiting_for_mfa")
                    deadline = time.monotonic() + (settings.VNU_VERIFICATION_WAIT_SECONDS if not browser_worker.headless else 0)
                    while requires_mfa(page) and time.monotonic() < deadline and not page.is_closed() and not browser_worker.stopping.is_set():
                        page.wait_for_timeout(750)
                    if requires_mfa(page):
                        return
                if not visible(page, 'input[type="password"]'):
                    return
                form_key = (id(page), public_url(page.url))
                if form_key in submitted_forms:
                    return
                if browser_worker.authenticated:
                    browser_worker.authenticated = False
                    if status_callback:
                        status_callback("session_expired")
                if status_callback:
                    status_callback("login_required")
                username_input = page.locator(
                    'input[type="text"], input[name*="user"], input[id*="user"]'
                ).first
                password_input = page.locator(
                    'input[type="password"], input[name*="pass"], input[id*="pass"]'
                ).first
                username_input.wait_for(state="visible", timeout=10000)
                password_input.wait_for(state="visible", timeout=10000)
                accept_browser_cookies(page)
                guard_action(page)
                username_input.fill(settings.VNU_LIBRARY_ID)
                guard_action(page)
                password_input.fill(settings.VNU_LIBRARY_PASSWORD)
                submitted_forms.add(form_key)
                guard_action(page)
                sign_in_button = page.get_by_role(
                    "button", name=re.compile(r"^Sign in$", re.IGNORECASE)
                ).last
                if sign_in_button.count() and sign_in_button.is_visible():
                    sign_in_button.click(timeout=5000)
                else:
                    password_input.press("Enter")
                deadline = time.monotonic() + 35
                while time.monotonic() < deadline and not page.is_closed() and not browser_worker.stopping.is_set():
                    if not trusted_login_page(page) or requires_mfa(page) or has_browser_verification(page):
                        break
                    error = page.get_by_text(re.compile(
                        r"incorrect (?:username|password)|invalid (?:username|password)|sign.?in failed", re.I))
                    if any(error.nth(i).is_visible() for i in range(min(error.count(), 4))):
                        break
                    page.wait_for_timeout(400)
                wait_for_stable_page(page, timeout_ms=30000)
                time.sleep(2)
                if page.is_closed():
                    return
            except Exception:
                return

    def collect_pdf_urls(page):
        selector = (
            'a[href], iframe[src], embed[src], object[data], '
            'meta[name="citation_pdf_url"], link[type*="pdf"], [data-article-pdf]'
        )
        script = """elements => elements.map(el => ({
            url: el.href || el.src || el.data || el.content || el.getAttribute('data-article-pdf') || el.getAttribute('data-url') || '',
            label: (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim(),
            kind: [el.tagName, el.getAttribute('type'), el.getAttribute('rel'), el.getAttribute('name'), el.getAttribute('data-article-pdf')].filter(Boolean).join(' ')
        })).filter(item => item.url)"""
        for _ in range(3):
            try:
                wait_for_stable_page(page, timeout_ms=5000)
                elements = page.locator(selector).evaluate_all(script)
                candidates = []
                for element in elements:
                    url = element.get("url", "")
                    label = element.get("label", "").lower()
                    lowered = url.lower()
                    kind = element.get("kind", "").lower()
                    score = 0
                    if ".pdf" in lowered or "pdf" in lowered or "pdf" in kind or "pdf" in label:
                        score += 5
                    if "stamp" in lowered or "pdfft" in lowered:
                        score += 5
                    if "download" in lowered or "download" in label:
                        score += 3
                    if "view pdf" in label:
                        score += 4
                    if "full text" in label or "full-text" in lowered:
                        score += 2
                    if any(w in label or w in lowered for w in ("purchase", "buy", "order", "rent", "cart", "pricing", "subscribe", "checkout")):
                        continue
                    if score:
                        candidates.append((score, url))
                candidates.sort(key=lambda item: item[0], reverse=True)
                return list(dict.fromkeys(url for _, url in candidates))[:12]
            except Exception:
                continue
        return []

    try:
        with browser_worker.session() as (browser, context), ExitStack() as capture_scope:
            page, resuming_verification = browser_worker.reusable_page(doi, resolution.get("provider"))
            if page is None:
                page = browser_worker.get_article_page()
            else:
                pid = browser_worker.get_page_id(page)
                print(f"[ARTICLE]\\nContinuing SAME page\\npage_id={pid}")
            pdf_capture = BrowserPdfCapture(context, output_path, progress_callback, tracked_pages=[page])
            # Remove event listeners before session() closes any article tabs.
            capture_scope.callback(pdf_capture.close)

            def save_captured_pdf():
                guard_action(page)
                return pdf_capture.save(page.url if not page.is_closed() else "")

            def await_verification(target):
                interactive = settings.VNU_ALLOW_MANUAL_VERIFICATION and not browser_worker.headless
                previously_challenged = target in browser_worker.pending_verifications.values()
                if not previously_challenged and verification_state(target) != "challenge":
                    return True

                with browser_worker._thread_auth_lock:
                    if not detect_cloudflare_challenge(target) and (is_sciencedirect_article_page(target) or previously_challenged):
                        if target in browser_worker.pending_verifications.values():
                            browser_worker.finish_verification(target)
                            browser_worker.save_state()
                            with _PUBLISHER_COOLDOWN_LOCK:
                                _publisher_cooldowns.pop(urlparse(target.url).hostname or "", None)
                                _publisher_cooldowns.pop("provider:" + provider_for_url(target.url), None)
                        return True

                    nonlocal current_state
                    current_state = "WAITING_FOR_HUMAN"
                    timeout = 600 if interactive else 0
                    if not interactive and previously_challenged and verification_state(target) != "challenge":
                        timeout = 7

                    success = wait_for_browser_verification(
                        target,
                        timeout_seconds=timeout,
                        status_callback=status_callback,
                        stop_event=browser_worker.stopping,
                        on_challenge=lambda target: browser_worker.pin_verification(target, doi, resolution.get("provider")),
                        previously_challenged=previously_challenged,
                        focus_event=browser_worker.focus_requested,
                    )
                    if success:
                        if target in browser_worker.pending_verifications.values():
                            browser_worker.finish_verification(target)
                            browser_worker.save_state()
                            with _PUBLISHER_COOLDOWN_LOCK:
                                _publisher_cooldowns.pop(urlparse(target.url).hostname or "", None)
                                _publisher_cooldowns.pop("provider:" + provider_for_url(target.url), None)
                        pid = browser_worker.get_page_id(target)
                        print(f"[BROWSER]\\nContext reused\\ncontext_id={browser_worker.context_id}")
                        print(f"[ARTICLE]\\nContinuing SAME page\\npage_id={pid}")
                    return success

            def guard_action(target=None):
                # Observe all tabs belonging to this DOI before any web action.
                targets = [candidate for candidate in context.pages
                           if candidate not in pdf_capture.existing_pages and not candidate.is_closed()
                           and not candidate.url.startswith(("chrome://", "edge://"))]
                if target is not None and target not in targets:
                    targets.append(target)
                for pending in browser_worker.pending_verifications.values():
                    if pending.is_closed():
                        raise _VerificationPaused(pending)
                for candidate in targets:
                    if detect_cloudflare_challenge(candidate) and not await_verification(candidate):
                        raise _VerificationPaused(candidate)
                pdf_capture.browser_only = browser_worker.challenge_session
                if browser_worker.stopping.is_set():
                    raise _VerificationPaused(target or page)

            pdf_capture.request_guard = guard_action

            def verification_failure(target):
                if browser_worker.stopping.is_set():
                    return False, "CANCELLED: Ứng dụng đang đóng; đã giữ profile đăng nhập."
                if target.is_closed():
                    return False, "CAPTCHA_REQUIRED: Cửa sổ xác minh đã đóng; không tự khởi động lại hoặc đổi browser."
                url = target.url if not target.is_closed() else vnu_url
                host = urlparse(url).hostname
                if host:
                    with _PUBLISHER_COOLDOWN_LOCK:
                        _publisher_cooldowns[host] = time.monotonic() + 600
                        provider = provider_for_url(url)
                        if provider != "Generic":
                            _publisher_cooldowns["provider:" + provider] = time.monotonic() + 600
                return False, f"CAPTCHA_REQUIRED: Tab xác minh được giữ nguyên. Hoàn tất xác minh trong cửa sổ đang mở, không đóng trình duyệt. URL='{public_url(url)}'."

            if progress_callback:
                progress_callback(5)
            try:
                # A pending challenge resumes on its pinned page. Navigating
                # to the DOI again here would discard the cleared challenge.
                if not resuming_verification:
                    guard_action(page)
                    if not is_sciencedirect_article_page(page):
                        current_state = "NAVIGATING"
                        page.goto(vnu_url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                # A redirect directly to an attachment aborts page navigation,
                # but the browser can still have started a successful download.
                if save_captured_pdf():
                    close_browser_safely(browser)
                    return True, f"Đã lưu PDF qua VNU: {output_path.name}"
                raise
            time.sleep(2)
            current_state = "CHECKING_CHALLENGE"
            wait_for_stable_page(page)
            if page.is_closed():
                if save_captured_pdf():
                    close_browser_safely(browser)
                    return True, f"\u0110\u00e3 l\u01b0u PDF qua VNU: {output_path.name}"
                close_browser_safely(browser)
                return False, "BROWSER_CLOSED: Trang đã đóng trong lúc chuyển qua OpenAthens."
            if progress_callback:
                progress_callback(20)

            if not await_verification(page):
                return verification_failure(page)
            
            current_state = "VERIFICATION_COMPLETED"
            
            # After verification: let browser redirect or continue naturally. No goto!
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            pid = browser_worker.get_page_id(page)
            print(f"[ARTICLE]\\nContinuing SAME page\\npage_id={pid}")

            current_state = "CHECKING_AUTH"

            # SSO may open another window, or redirect through several domains.
            # Follow that window rather than querying the stale article DOM.
            seen_auth_steps = set()
            for _ in range(6):
                page = active_auth_page(page)
                if page.is_closed():
                    break
                wait_for_stable_page(page)
                step = (id(page), page.url)
                if step in seen_auth_steps:
                    break
                seen_auth_steps.add(step)
                if not await_verification(page):
                    return verification_failure(page)
                if trusted_login_page(page):
                    authenticate_vnu_redirect(page)
                    if requires_mfa(page):
                        break
                elif is_authenticated(page) or has_fulltext_access(page):
                    break
                else:
                    select_sciencedirect_institution(page)
                next_page = active_auth_page(page)
                wait_for_stable_page(next_page)
                if (id(next_page), next_page.url) == step:
                    break
                page = next_page
            if save_captured_pdf():
                close_browser_safely(browser)
                return True, f"Đã lưu PDF qua tài khoản VNU: {output_path.name}"
            if not await_verification(page):
                return verification_failure(page)
            if page.is_closed():
                if save_captured_pdf():
                    close_browser_safely(browser)
                    return True, f"\u0110\u00e3 l\u01b0u PDF qua VNU: {output_path.name}"
                close_browser_safely(browser)
                return False, "BROWSER_CLOSED: Trang đã đóng sau bước đăng nhập VNU."
            if requires_mfa(page):
                if status_callback:
                    status_callback("waiting_for_mfa")
                return False, "MFA_REQUIRED: Chưa hoàn tất xác minh tài khoản VNU trong thời gian chờ."
            if trusted_login_page(page) and visible(page, 'input[type="password"]'):
                return False, "LOGIN_REQUIRED: Đăng nhập VNU chưa hoàn tất; kiểm tra tài khoản hoặc phiên đăng nhập."
            if is_authenticated(page):
                browser_worker.authenticated = True
                if status_callback:
                    status_callback("authenticated")
                browser_worker.save_state()
            if has_fulltext_access(page) and status_callback:
                status_callback("institution_access")
            if not trusted_login_page(page):
                host = urlparse(page.url).hostname or ""
                if host and host not in ("doi.org", "dx.doi.org"):
                    record = get_store().get(doi)
                    get_store().update(doi, (record or {}).get("status", "PROCESSING"),
                                       resolved_url=page.url, hostname=host, provider=provider_for_url(page.url))
            if progress_callback:
                progress_callback(55)

            current_state = "CHECKING_ACCESS"
            guard_action(page)
            accept_browser_cookies(page)

            if save_captured_pdf():
                close_browser_safely(browser)
                return True, f"\u0110\u00e3 l\u01b0u PDF qua VNU: {output_path.name}"

            # Kiểm tra trang lỗi DOI Not Found
            wait_for_stable_page(page)
            try:
                page_title = page.title().lower()
                page_url = page.url.lower()
            except Exception:
                wait_for_stable_page(page, timeout_ms=10000)
                page_title = page.title().lower()
                page_url = page.url.lower()
            if "doi not found" in page_title:
                close_browser_safely(browser)
                return False, f"Mã DOI '{doi}' không tồn tại trên hệ thống xuất bản quốc tế (DOI Not Found)."

            view_pdf_url = None
            guard_action(page)
            if "sciencedirect.com" in page.url.lower():
                article_url = page.url
                view_pdf_button = page.locator(
                    'a:has-text("View PDF"), button:has-text("View PDF"), '
                    'a[aria-label*="View PDF" i], button[aria-label*="View PDF" i]'
                ).first
                try:
                    view_pdf_button.wait_for(state="visible", timeout=4000)
                    guard_action(page)
                    with page.expect_popup(timeout=10000) as popup_info:
                        view_pdf_button.click(force=True)
                    pdf_view_tab = popup_info.value
                except Exception:
                    pdf_view_tab = page

                try:
                    wait_for_stable_page(pdf_view_tab, timeout_ms=15000)
                    if not await_verification(pdf_view_tab):
                        return verification_failure(pdf_view_tab)
                    if pdf_view_tab is not page or pdf_view_tab.url != article_url:
                        view_pdf_url = pdf_view_tab.url
                    if save_captured_pdf():
                        close_browser_safely(browser)
                        return True, f"Đã lưu PDF mở bằng View PDF qua VNU: {output_path.name}"
                    if view_pdf_url and view_pdf_url.startswith(("https://", "http://")):
                        guard_action(pdf_view_tab)
                        if pdf_capture.save_url(view_pdf_url, page.url):
                            if progress_callback:
                                progress_callback(100)
                            close_browser_safely(browser)
                            return True, f"Đã lưu PDF mở bằng View PDF qua VNU: {output_path.name}"
                except Exception:
                    pass

            # Tìm nút PDF hoặc link tải PDF
            publisher_pdf_selector = (
                'a[href*="/pdfft"], a[href*="/pdf"], a[aria-label*="Download PDF" i], '
                'button[aria-label*="Download PDF" i], a:has-text("Download PDF"), button:has-text("Download PDF"), '
                'a[href*="/stamp/"], a.pdf-btn, a[href*=".pdf"], a[data-article-pdf], a[download], '
                'a[aria-label*="pdf" i], button[aria-label*="pdf" i], a:has-text("PDF"), button:has-text("PDF"), '
                'a:has-text("Download"), button:has-text("Download"), a:has-text("Full Text"), button:has-text("Full Text")'
            )
            
            all_pdf_elements = page.locator(publisher_pdf_selector)
            pdf_element = None
            try:
                all_pdf_elements.first.wait_for(state="attached", timeout=8000)
                count = all_pdf_elements.count()
                for i in range(count):
                    candidate = all_pdf_elements.nth(i)
                    try:
                        txt = (candidate.inner_text() or "").lower()
                        aria = (candidate.get_attribute("aria-label") or "").lower()
                        href = (candidate.get_attribute("href") or "").lower()
                        combined = f"{txt} {aria} {href}"
                        # "bỏ cái bấm cái nút purse pdf đi" -> Ignore Purchase PDF!
                        if any(w in combined for w in ("purchase", "buy", "order", "rent", "cart", "checkout", "subscribe", "pricing")):
                            continue
                        if candidate.is_visible():
                            pdf_element = candidate
                            break
                    except Exception:
                        continue
            except Exception:
                pass

            if pdf_element:
                guard_action(page)
                if progress_callback:
                    progress_callback(70)
                try:
                    href = pdf_element.get_attribute("href", timeout=5000)
                except Exception:
                    wait_for_stable_page(page, timeout_ms=10000)
                    pdf_element = page.locator(publisher_pdf_selector).first
                    href = pdf_element.get_attribute("href", timeout=5000)
                try:
                    if progress_callback:
                        progress_callback(80)
                    guard_action(page)
                    with page.expect_download(timeout=15000) as download_info:
                        pdf_element.click(force=True)
                    download = download_info.value
                    if progress_callback:
                        progress_callback(90)
                    if not save_browser_download(download):
                        raise ValueError("Publisher download was not a valid PDF")
                    if progress_callback:
                        progress_callback(100)
                    close_browser_safely(browser)
                    return True, f"Đã tự động tải thành công qua VNU: {output_path.name}"
                except Exception:
                    if save_captured_pdf():
                        close_browser_safely(browser)
                        return True, f"\u0110\u00e3 l\u01b0u PDF qua VNU: {output_path.name}"
                    if href and ("stamp" in href or "pdf" in href):
                        from urllib.parse import urljoin
                        full_stamp = urljoin(page.url, href)
                        guard_action(page)
                        stamp_page = browser_worker.get_auth_page()
                        try:
                            with stamp_page.expect_download(timeout=25000) as dl_info:
                                guard_action(stamp_page)
                                stamp_page.goto(full_stamp)
                            download = dl_info.value
                            if progress_callback:
                                progress_callback(90)
                            if not save_browser_download(download):
                                raise ValueError("Publisher download was not a valid PDF")
                            if progress_callback:
                                progress_callback(100)
                            close_browser_safely(browser)
                            return True, f"Đã tự động tải thành công qua VNU: {output_path.name}"
                        except Exception:
                            pass
                        guard_action(stamp_page)

            # Some publishers expose a PDF URL in metadata, an iframe, or a
            # download link rather than a visible PDF button. Fetch candidates
            # through the authenticated browser context so VNU cookies apply.
            from urllib.parse import urljoin
            candidate_urls = list(dict.fromkeys(pdf_capture.urls + collect_pdf_urls(page)))
            if view_pdf_url:
                candidate_urls.insert(0, view_pdf_url)
            if progress_callback:
                progress_callback(70)
            # IEEE's article page often exposes its PDF through the authenticated
            # stamp endpoint instead of a normal .pdf anchor. Keep these requests
            # inside the Playwright context so the VNU/IEEE session cookies apply.
            ieee_match = re.search(r"(?:/document/|arnumber=)(\d{6,})", page.url, re.IGNORECASE)
            if "ieeexplore.ieee.org" in page.url.lower() and ieee_match:
                arnumber = ieee_match.group(1)
                ieee_pdf_urls = [
                    f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={arnumber}&ref=",
                    f"https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={arnumber}",
                ]
                candidate_urls = list(dict.fromkeys(ieee_pdf_urls + candidate_urls))

            attempt_results = []
            for candidate_url in candidate_urls[:8]:
                candidate_url = urljoin(page.url, candidate_url)
                guard_action(page)
                if provider_for_url(candidate_url) == "Elsevier" or pdf_capture.browser_only:
                    if pdf_capture.save_url(candidate_url, page.url):
                        return True, f"Đã lưu PDF trong phiên trình duyệt VNU: {output_path.name}"
                    attempt_results.append("Phiên trình duyệt chưa nhận được PDF từ link đã tìm thấy.")
                    continue
                try:
                    response = context.request.get(
                        candidate_url,
                        headers={"Referer": page.url, "Accept": "application/pdf,*/*"},
                        timeout=15000
                    )
                    body = response.body()
                    if response.status == 200 and pdf_capture.save_body(body):
                        response.dispose()
                        close_browser_safely(browser)
                        return True, f"\u0110\u00e3 l\u01b0u PDF qua VNU: {output_path.name}"
                    attempt_results.append(f"{response.status} {response.headers.get('content-type', '')} {response.url}")
                    # stamp.jsp is an HTML wrapper. Follow only its IEEE-hosted
                    # iframe/embed target, then still require an actual PDF body.
                    if ("ieeexplore.ieee.org/stamp/" in response.url.lower()
                            and response.status == 200 and body[:512].lstrip().startswith(b"<")):
                        stamp_soup = BeautifulSoup(body, "html.parser")
                        embedded_urls = []
                        for element in stamp_soup.select("iframe[src], embed[src], object[data]"):
                            embedded = element.get("src") or element.get("data")
                            if embedded:
                                embedded_urls.append(urljoin(response.url, embedded))
                        for embedded_url in embedded_urls:
                            if urlparse(embedded_url).hostname != "ieeexplore.ieee.org":
                                continue
                            guard_action(page)
                            try:
                                embedded_response = context.request.get(
                                    embedded_url,
                                    headers={"Referer": response.url, "Accept": "application/pdf,*/*"},
                                    timeout=15000
                                )
                                embedded_body = embedded_response.body()
                                attempt_results.append(
                                    f"{embedded_response.status} {embedded_response.headers.get('content-type', '')} {embedded_response.url}"
                                )
                                if embedded_response.status == 200 and pdf_capture.save_body(embedded_body):
                                    embedded_response.dispose()
                                    response.dispose()
                                    close_browser_safely(browser)
                                    return True, f"\u0110\u00e3 l\u01b0u PDF qua VNU: {output_path.name}"
                                embedded_response.dispose()
                            except Exception as embedded_exc:
                                attempt_results.append(f"IEEE embedded request: {embedded_exc}")
                    response.dispose()
                except Exception as request_exc:
                    attempt_results.append(f"{candidate_url}: {request_exc}")
                    continue

            if save_captured_pdf():
                close_browser_safely(browser)
                return True, f"\u0110\u00e3 l\u01b0u PDF qua VNU: {output_path.name}"

            try:
                publisher_url = page.url
                page_title = page.title()
            except Exception:
                publisher_url, page_title = "unknown page", "unknown title"
            publisher_denied = any("denied" in result.lower() or "access denied" in result.lower()
                                   for result in attempt_results)
            publisher_forbidden = any(re.search(r"\b403\b", result) for result in attempt_results)
            close_browser_safely(browser)
            return False, (
                ("NO_ACCESS: " if publisher_denied or publisher_forbidden else "FAILED: ") +
                f"\u0110\u00e3 v\u00e0o VNU nh\u01b0ng ch\u01b0a l\u1ea5y \u0111\u01b0\u1ee3c PDF. "
                f"Bot \u0111\u00e3 th\u1eed {len(candidate_urls)} link PDF/t\u1ea3i; trang='{page_title}', URL='{publisher_url}'. "
                + (f"Ph\u1ea3n h\u1ed3i: {'; '.join(attempt_results[-4:])}. " if attempt_results else "")
                + ("Nh\u00e0 xu\u1ea5t b\u1ea3n tr\u1ea3 v\u1ec1 trang t\u1eeb ch\u1ed1i quy\u1ec1n; g\u00f3i VNU c\u00f3 th\u1ec3 kh\u00f4ng bao g\u1ed3m b\u00e0i n\u00e0y."
                   if publisher_denied else
                   "Nh\u00e0 xu\u1ea5t b\u1ea3n tr\u1ea3 HTTP 403 cho y\u00eau c\u1ea7u PDF; h\u00e3y m\u1edf trang b\u00e0i b\u00e1o trong tr\u00ecnh duy\u1ec7t \u0111\u1ec3 ki\u1ec3m tra phi\u00ean VNU v\u00e0 quy\u1ec1n thu\u00ea bao."
                   if publisher_forbidden else
                   "Phi\u00ean th\u01b0 vi\u1ec7n c\u00f3 th\u1ec3 kh\u00f4ng \u0111\u01b0\u1ee3c c\u1ea5p quy\u1ec1n to\u00e0n v\u0103n b\u00e0i n\u00e0y.")
            )

    except _VerificationPaused as pause:
        return verification_failure(pause.page)
    except Exception as e:
        if browser_worker.challenge_session and type(e).__name__ == "TargetClosedError":
            return False, "CAPTCHA_REQUIRED: Browser đã đóng sau xác minh; không tự đổi phiên hoặc thử lại DOI."
        if str(e).startswith("Configured browser "):
            return False, "BROWSER_CONFIG: " + str(e)
        code = "BROWSER_CLOSED" if type(e).__name__ == "TargetClosedError" else "FAILED"
        return False, f"{code}: Lỗi quá trình tự động: {type(e).__name__}."
    finally:
        if pdf_capture:
            pdf_capture.close()

async def auto_download_vnu(doi: str, output_filename: str, progress_callback=None, status_callback=None) -> Tuple[bool, str]:
    """Hàm wrapper bất đồng bộ chạy Playwright trong thread pool để không block server."""
    for attempt in range(2):
        success, message = await browser_worker.run(_run_sync_vnu, doi, output_filename, progress_callback, status_callback)
        if success or attempt > 0:
            break
        if browser_worker.challenge_session or "CAPTCHA_REQUIRED" in message or "WAITING_FOR_HUMAN" in message or "MANUAL_VERIFICATION_TIMEOUT" in message:
            break
        if not message.startswith("BROWSER_CLOSED:"):
            break
        if not browser_worker.browser or not browser_worker.browser.is_connected():
            if status_callback:
                status_callback("reopening_browser")
            await browser_worker.run(browser_worker.recover_after_close)
        else:
            break
    return success, safe_message(message)


_DOWNLOAD_LOCKS = {}
_DOWNLOAD_LOCKS_GUARD = Lock()
_PUBLIC_LIMIT = __import__("threading").Semaphore(2)
_RESOLVE_LIMIT = __import__("threading").Semaphore(6)


def download_locks(doi, target):
    keys = ("doi:" + doi, "path:" + os.path.normcase(str(target.resolve())))
    with _DOWNLOAD_LOCKS_GUARD:
        return [_DOWNLOAD_LOCKS.setdefault(key, Lock()) for key in sorted(keys)]


def import_verified_pdf(doi, source, target):
    """Import an explicitly selected PDF using the same locks as auto-download."""
    import shutil
    from contextlib import ExitStack
    store = get_store()
    with ExitStack() as stack:
        for lock in download_locks(doi, target):
            stack.enter_context(lock)
        owner = store.file_owner(target)
        if target.exists() or (owner and owner != doi):
            return False, "Tên file đã tồn tại hoặc thuộc DOI khác."
        if not valid_pdf_file(source):
            return False, "INVALID_PDF: File nguồn không phải PDF đầy đủ."
        partial = target.with_suffix(target.suffix + ".part")
        store.update(doi, "PROCESSING", filename=target.name, file_path=str(target))
        try:
            shutil.copy2(source, partial)
            if not valid_pdf_file(partial):
                store.update(doi, "INVALID_PDF", message="File nguồn thay đổi trong khi import.")
                return False, "INVALID_PDF: File nguồn thay đổi trong khi import."
            partial.replace(target)
            store.update(doi, "DOWNLOADED", source="manual_import", message="Đã import PDF được chọn.")
            return True, "Đã import PDF được chọn."
        except OSError as error:
            store.update(doi, "FAILED", message=type(error).__name__)
            return False, "Import thất bại: " + type(error).__name__
        finally:
            partial.unlink(missing_ok=True)


async def _bounded_thread(limit, function, *args):
    while not limit.acquire(blocking=False):
        await asyncio.sleep(0.1)
    try:
        return await asyncio.to_thread(function, *args)
    finally:
        limit.release()


def _public_download(*args):
    from automatic_sources import download_public_copy
    return download_public_copy(*args)


_PIPELINE_JOBS = set()


async def auto_download_paper(*args, **kwargs):
    task = asyncio.create_task(_auto_download_paper(*args, **kwargs))
    _PIPELINE_JOBS.add(task)
    task.add_done_callback(_PIPELINE_JOBS.discard)
    return await asyncio.shield(task)


async def _auto_download_paper(doi: str, output_filename: str, title="", progress_callback=None, status_callback=None, oa_url=None):
    """Quick OA links, institutional web access, then public discovery; no Elsevier API."""
    from doi_resolver import resolve_doi
    from automatic_sources import known_public_copy

    doi = extract_doi(doi)
    if not doi:
        return False, "INVALID_DOI: DOI không hợp lệ.", "invalid"
    try:
        target = download_path(settings.DOWNLOAD_FOLDER, output_filename)
    except ValueError as error:
        return False, str(error), "invalid"
    locks = download_locks(doi, target)
    acquired = []
    store = get_store()
    try:
        for lock in locks:
            while not lock.acquire(blocking=False):
                await asyncio.sleep(0.1)
            acquired.append(lock)
        existing = store.downloaded(doi)
        if existing:
            if progress_callback:
                progress_callback(100)
            return True, "Đã có PDF được kiểm tra: " + existing["filename"], "existing"
        owner = store.file_owner(target)
        if owner and owner != doi:
            return False, "Tên file đang thuộc DOI khác; hãy đổi tên file.", "invalid"
        if owner == doi and valid_pdf_file(target):
            store.update(doi, "DOWNLOADED", message="Đã khôi phục PDF đầy đủ từ lần tải trước.")
            if progress_callback:
                progress_callback(100)
            return True, "Đã khôi phục PDF đầy đủ từ lần tải trước.", "existing"
        if valid_pdf_file(target) and not owner:
            return False, "File PDF đã tồn tại nhưng chưa xác định DOI; hãy dùng tên khác để tránh ghi đè.", "invalid"
        store.update(doi, "PROCESSING", filename=target.name, file_path=str(target), metadata={"title": title})
        if browser_worker.stopping.is_set():
            message = "CANCELLED: Ứng dụng đang đóng; có thể tiếp tục danh sách sau khi mở lại."
            store.update(doi, "CANCELLED", message=message)
            return False, message, "unavailable"

        def status(value):
            states = {"waiting_for_verification": "CAPTCHA_REQUIRED", "waiting_for_mfa": "MFA_REQUIRED",
                      "login_required": "LOGIN_REQUIRED", "authenticated": "AUTHENTICATED", "session_expired": "SESSION_EXPIRED",
                      "institution_access": "INSTITUTION_ACCESS", "publisher_cooldown": "CAPTCHA_REQUIRED",
                      "verification_complete": "PROCESSING"}
            if value in states:
                store.update(doi, states[value])
            if status_callback:
                status_callback(value)

        def progress(value):
            # Final 100 is sent only after PDF validation and catalog commit.
            if progress_callback:
                progress_callback(min(99, value) if value is not None else None)

        def finished(message, method):
            if not valid_pdf_file(target):
                store.update(doi, "INVALID_PDF", message="Nguồn trả về file không phải PDF đầy đủ.")
                return False, "INVALID_PDF: Nguồn chưa trả về PDF đầy đủ.", method
            if method in ("oa", "public_copy"):
                store.update(doi, "OPEN_ACCESS", source=method)
            store.update(doi, "DOWNLOADED", source=method, message=message)
            if progress_callback:
                progress_callback(100)
            return True, safe_message(message), method

        resolution = await _bounded_thread(_RESOLVE_LIMIT, resolve_doi, doi)
        messages = []
        status("checking_public_sources")
        known = known_public_copy(doi)
        quick_url = oa_url or known.get("url")
        if quick_url:
            with_url = await _bounded_thread(_PUBLIC_LIMIT, download_file_direct, quick_url, target,
                                              lambda percent, received, total: progress(percent))
            if with_url[0]:
                message = with_url[1] if oa_url else f"Đã tải PDF từ {known.get('source') or 'kho tác giả'}."
                return finished(message, "oa" if oa_url else "public_copy")
            messages.append(with_url[1])
        if resolution.get("status") == "INVALID_DOI":
            message = resolution.get("message", "DOI không tồn tại.")
            store.update(doi, "INVALID_DOI", message=message)
            return False, message, "invalid"
        if publisher_is_cooling_down(resolution) and doi not in browser_worker.pending_verifications:
            status("publisher_cooldown")
            messages.append("Nhà xuất bản đang tạm nghỉ sau CAPTCHA; không lặp lại xác minh.")
            final_state = "CAPTCHA_REQUIRED"
        elif settings.VNU_LIBRARY_ID and settings.VNU_LIBRARY_PASSWORD:
            status("public_sources_finished")
            status("checking_vnu")
            success, message = await auto_download_vnu(doi, target.name, progress, status)
            if success:
                return finished(message, "vnu")
            messages.append(message)
            if message.startswith("CANCELLED:"):
                store.update(doi, "CANCELLED", message=message)
                return False, message, "unavailable"
            prior = store.get(doi)["status"]
            final_state = prior if prior in ("CAPTCHA_REQUIRED", "MFA_REQUIRED", "LOGIN_REQUIRED") else ("NO_ACCESS" if message.startswith("NO_ACCESS:") else "FAILED")
        else:
            messages.append("Chưa cấu hình tài khoản VNU.")
            final_state = "LOGIN_REQUIRED"
        # Institutional web rights are independent of the developer API key.
        # If the browser cannot get a PDF, release its worker and search OA.
        status("public_fallback_started")
        success, message = await _bounded_thread(_PUBLIC_LIMIT, _public_download, doi, title, target, progress, status)
        if success:
            return finished(message, "public_copy")
        messages.append(message)
        prior = (store.get(doi) or {}).get("status")
        if prior in ("CAPTCHA_REQUIRED", "MFA_REQUIRED", "LOGIN_REQUIRED"):
            final_state = prior
        message = safe_message(" ".join(messages))
        store.update(doi, final_state, message=message)
        return False, message, "unavailable"
    except asyncio.CancelledError:
        # Sync requests may finish after the client disconnects. Preserve a
        # resumable state; next request verifies the file and catalog again.
        store.update(doi, "CANCELLED", message="Kết nối tải đã đóng; có thể tiếp tục từ danh sách DOI.")
        raise
    except Exception as error:
        message = "Lỗi tải: " + type(error).__name__
        store.update(doi, "FAILED", message=message)
        return False, message, "unavailable"
    finally:
        for lock in reversed(acquired):
            lock.release()


def fetch_crossref_ris(doi: str, timeout: int = 5) -> Optional[str]:
    """Lấy trực tiếp bản ghi trích dẫn RIS chuẩn từ CrossRef / DOI Registrar."""
    if not doi or not doi.startswith("10."):
        return None
    url = f"https://doi.org/{doi}"
    headers = {
        "Accept": "application/x-research-info-systems",
        "User-Agent": "SLR-Paper-Fetcher/1.0 (mailto:academic-research@vnulib.edu.vn)"
    }
    try:
        r = polite_get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if r.status_code == 200 and "TY  -" in r.text:
            text = r.text.strip()
            # Đảm bảo kết thúc bằng thẻ ER  -
            if not text.endswith("ER  -"):
                if "ER  -" not in text:
                    text += "\nER  -"
            return text
    except Exception:
        pass
    return None

def build_fallback_ris(doi: str = "", title: str = "", authors: Any = None, journal: str = "", year: str = "", url: str = "") -> str:
    """Tạo bản ghi RIS tiêu chuẩn từ metadata sẵn có nếu không gọi được CrossRef."""
    lines = ["TY  - JOUR"]
    if title:
        lines.append(f"TI  - {title.strip()}")
        lines.append(f"T1  - {title.strip()}")
    if authors:
        if isinstance(authors, str):
            author_list = [a.strip() for a in re.split(r'[,;]\s*', authors) if a.strip()]
        else:
            author_list = [str(a).strip() for a in authors if str(a).strip()]
        for au in author_list:
            lines.append(f"AU  - {au}")
    if journal:
        lines.append(f"JO  - {journal.strip()}")
        lines.append(f"T2  - {journal.strip()}")
    if year:
        lines.append(f"PY  - {str(year).strip()}")
    if doi:
        clean_d = doi.strip()
        lines.append(f"DO  - {clean_d}")
        if not url:
            url = f"https://doi.org/{clean_d}"
    if url:
        lines.append(f"UR  - {url.strip()}")
    lines.append("ER  -")
    return "\n".join(lines)

def get_paper_ris(item: Dict[str, Any]) -> str:
    """Lấy bản ghi RIS cho một bài báo (ưu tiên CrossRef chính thống, fallback cấu trúc chuẩn)."""
    raw_doi = item.get("doi") or item.get("original") or ""
    doi = extract_doi(raw_doi)
    
    if doi and doi.startswith("10."):
        ris_text = fetch_crossref_ris(doi)
        if ris_text:
            return ris_text

    # Fallback khi không kết nối được hoặc DOI không có trong CrossRef
    title = item.get("title") or item.get("custom_title") or ""
    journal = item.get("journal") or item.get("source") or ""
    year = item.get("year") or ""
    authors = item.get("authors") or []
    url = item.get("oa_link") or item.get("vnu_link") or (f"https://doi.org/{doi}" if doi else "")
    return build_fallback_ris(doi=doi, title=title, authors=authors, journal=journal, year=year, url=url)

async def generate_combined_ris(items: list) -> str:
    """Tạo nội dung file RIS tổng hợp cho danh sách các bài báo bằng cách chạy song song đa luồng."""
    import concurrent.futures

    loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        tasks = [loop.run_in_executor(pool, get_paper_ris, item) for item in items]
        records = await asyncio.gather(*tasks)

    valid_records = [r.strip() for r in records if r and r.strip()]
    return "\n\n".join(valid_records) + "\n"

