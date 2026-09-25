import os
import re
import time
import requests
import asyncio
from pathlib import Path
from bs4 import BeautifulSoup
from typing import Optional, Tuple, Dict, Any
from config import settings

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
}

def extract_doi(text: str) -> str:
    """Trích xuất mã DOI chuẩn từ URL hoặc chuỗi văn bản."""
    if not text:
        return ""
    text = str(text).strip()
    match = re.search(r'(10\.\d{4,9}/[-._;()/:A-Z0-9]+)', text, re.IGNORECASE)
    if match:
        return match.group(1).rstrip('.')
    return text

def check_unpaywall(doi: str) -> dict:
    """Kiểm tra nguồn mở Open Access chính thống qua Unpaywall."""
    url = f"https://api.unpaywall.org/v2/{doi}?email={settings.UNPAYWALL_EMAIL}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=7)
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
        r = requests.get(url, headers=HEADERS, timeout=6)
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

def check_direct_mirrors(doi: str) -> Tuple[Optional[str], Optional[str]]:
    """Tìm bản full text PDF trực tiếp qua các Academic Mirrors (bỏ qua paywall bot)."""
    # 1. Direct bban mirror (máy chủ lưu trữ PDF trực tiếp của Sci-Hub)
    bban_url = f"https://sci.bban.top/pdf/{doi}.pdf"
    try:
        r = requests.head(bban_url, headers=HEADERS, timeout=5, allow_redirects=True)
        if r.status_code == 200 and ("pdf" in r.headers.get("content-type", "").lower() or int(r.headers.get("content-length", 0)) > 2000):
            return bban_url, "Bypass Paywall (Direct Mirror)"
    except Exception:
        pass

    # 2. Quét qua các cổng Sci-Hub đang hoạt động
    mirrors = [
        "https://sci-hub.ren",
        "https://sci-hub.wf",
        "https://sci-hub.se",
        "https://sci-hub.st",
        "https://sci-hub.ru"
    ]
    for mirror in mirrors:
        try:
            target = f"{mirror}/{doi}"
            r = requests.get(target, headers=HEADERS, timeout=6)
            if r.status_code == 200 and ("pdf" in r.text.lower() or "application/pdf" in r.text):
                # Regex tìm link embed src hoặc iframe src
                m = re.search(r'src=["\']([^"\']+\.pdf[^"\']*)["\']', r.text)
                if m:
                    src = m.group(1)
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = mirror + src
                    return src, "Bypass Paywall (Sci-Hub)"

                soup = BeautifulSoup(r.text, "html.parser")
                embed = soup.find(["embed", "iframe"], id="pdf")
                if embed and embed.get("src"):
                    src = embed["src"]
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = mirror + src
                    return src, "Bypass Paywall (Sci-Hub)"
        except Exception:
            continue

    return None, None

def find_paper_fulltext(doi: str) -> Dict[str, Any]:
    """
    Quy trình tích hợp đa tầng để tìm link tải PDF trực tiếp:
    1. Kiểm tra Unpaywall (Gold / Green OA)
    2. Kiểm tra Semantic Scholar OA
    3. Kiểm tra Direct Academic Mirrors (Bypass Paywall trực tiếp)
    """
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

    # 3. Direct Mirrors (Bỏ qua Paywall bot tải trực tiếp)
    direct_pdf, mirror_source = check_direct_mirrors(doi)
    if direct_pdf:
        return {
            "found": True,
            "has_pdf": True,
            "pdf_url": direct_pdf,
            "landing_url": unpaywall_info.get("landing_url"),
            "title": title,
            "source": mirror_source,
            "status": "Bypass Paywall"
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

def download_file_direct(url: str, output_path: Path) -> Tuple[bool, str]:
    """Tải trực tiếp file PDF từ URL với đa cơ chế (Requests + fallback curl)."""
    if not url or not url.startswith("http"):
        return False, "URL tải không hợp lệ."

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Thêm Referer phù hợp tránh bị CDN chặn
    custom_headers = dict(HEADERS)
    if "bban.top" in url or "sci-hub" in url:
        custom_headers["Referer"] = "https://sci-hub.ren/"
    elif "ieee.org" in url:
        custom_headers["Referer"] = "https://ieeexplore.ieee.org/"

    # Cơ chế 1: Thử tải bằng requests
    try:
        r = requests.get(url, headers=custom_headers, timeout=25, allow_redirects=True, stream=True)
        if r.status_code == 200:
            content = r.content
            if content.startswith(b"%PDF") or (len(content) > 3000 and not content.strip().startswith(b"<!DOC") and not content.strip().startswith(b"<htm")):
                with open(output_path, "wb") as f:
                    f.write(content)
                return True, "Tải thành công."
    except Exception as e:
        print(f"Requests download warning for {url}: {e}")

    # Cơ chế 2: Fallback qua curl.exe (vượt qua Fastly / Cloudflare / CDN Bot Management)
    try:
        import subprocess
        temp_out = output_path.with_suffix(".tmp")
        cmd = [
            "curl.exe", "-L", "-s", "-k",
            "-o", str(temp_out),
            "-A", custom_headers["User-Agent"],
            "-H", "Accept: application/pdf,text/html,*/*",
            "-e", custom_headers.get("Referer", ""),
            url
        ]
        subprocess.run(cmd, capture_output=True, timeout=30)
        if temp_out.exists() and temp_out.stat().st_size > 1000:
            with open(temp_out, "rb") as f:
                header = f.read(10)
            if header.startswith(b"%PDF") or (temp_out.stat().st_size > 3000 and not header.strip().startswith(b"<!DOC") and not header.strip().startswith(b"<htm")):
                if output_path.exists():
                    output_path.unlink()
                temp_out.rename(output_path)
                return True, "Tải thành công (curl)."
        if temp_out.exists():
            temp_out.unlink()
    except Exception as e:
        print(f"Curl download warning for {url}: {e}")

    return False, "Không thể tải file tự động. Bạn có thể mở link bài báo trực tiếp để tải."

def _run_sync_vnu(doi: str, output_filename: str) -> Tuple[bool, str]:
    """Hàm đồng bộ chạy Playwright trong luồng độc lập, tránh xung đột asyncio loop trên Windows."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "Playwright chưa được cài đặt hoàn tất."

    output_path = settings.DOWNLOAD_FOLDER / output_filename
    vnu_url = f"{settings.VNU_OPENATHENS_BASE_URL}https://doi.org/{doi}"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                accept_downloads=True,
                user_agent=HEADERS["User-Agent"]
            )
            page = context.new_page()

            # Điều hướng tới OpenAthens VNU
            page.goto(vnu_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2000)

            # Kiểm tra nếu xuất hiện trang đăng nhập OpenAthens / IDP của VNU
            current_url = page.url.lower()
            if "openathens" in current_url or "idp." in current_url or "login" in current_url or "auth" in current_url:
                # Đóng / bỏ qua cookie banner nếu có
                cookie_btn = page.query_selector('#ccc-close, #ccc-recommended-settings, button#ccc-notify-accept')
                if cookie_btn:
                    try:
                        cookie_btn.click(force=True)
                    except Exception:
                        pass

                username_input = page.wait_for_selector('input[type="text"], input[name*="user"], input[id*="user"]', timeout=10000)
                password_input = page.wait_for_selector('input[type="password"], input[name*="pass"], input[id*="pass"]', timeout=10000)
                if username_input and password_input:
                    username_input.fill(settings.VNU_LIBRARY_ID)
                    password_input.fill(settings.VNU_LIBRARY_PASSWORD)
                    password_input.press("Enter")

                try:
                    page.wait_for_url(lambda u: "openathens" not in u.lower() and "idp." not in u.lower(), timeout=35000)
                except Exception:
                    pass

                page.wait_for_load_state("domcontentloaded", timeout=30000)
                page.wait_for_timeout(3500)

            # Kiểm tra trang lỗi DOI Not Found
            page_title = page.title().lower()
            page_url = page.url.lower()
            if "doi not found" in page_title or "error" in page_title or ("doi.org" in page_url and "ieeexplore" not in page_url and "sciencedirect" not in page_url):
                browser.close()
                return False, f"Mã DOI '{doi}' không tồn tại trên hệ thống xuất bản quốc tế (DOI Not Found)."

            # Tìm nút PDF hoặc link tải PDF
            pdf_element = page.query_selector(
                'a[href*="/stamp/"], a.pdf-btn, a[href*=".pdf"], a[data-article-pdf], a:has-text("PDF"), button:has-text("PDF"), a:has-text("Download PDF"), button:has-text("Download PDF")'
            )

            if pdf_element:
                href = pdf_element.get_attribute("href")
                try:
                    with page.expect_download(timeout=15000) as download_info:
                        pdf_element.click(force=True)
                    download = download_info.value
                    download.save_as(str(output_path))
                    browser.close()
                    return True, f"Đã tự động tải thành công qua VNU: {output_path.name}"
                except Exception:
                    if href and ("stamp" in href or "pdf" in href):
                        from urllib.parse import urljoin
                        full_stamp = urljoin(page.url, href)
                        stamp_page = context.new_page()
                        try:
                            with stamp_page.expect_download(timeout=25000) as dl_info:
                                stamp_page.goto(full_stamp)
                            download = dl_info.value
                            download.save_as(str(output_path))
                            browser.close()
                            return True, f"Đã tự động tải thành công qua VNU: {output_path.name}"
                        except Exception:
                            pass

            browser.close()
            return False, "Đăng nhập VNU thành công nhưng trang nhà xuất bản này chưa xuất hiện nút PDF trực tiếp. Bạn vui lòng bấm 'Mở link' để xem trực tiếp."

    except Exception as e:
        return False, f"Lỗi quá trình tự động: {str(e)}"

async def auto_download_vnu(doi: str, output_filename: str) -> Tuple[bool, str]:
    """Hàm wrapper bất đồng bộ chạy Playwright trong thread pool để không block server."""
    return await asyncio.to_thread(_run_sync_vnu, doi, output_filename)
