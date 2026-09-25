import os
import re
import time
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from typing import Optional, Tuple
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
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            data = r.json()
            is_oa = data.get("is_oa", False)
            title = data.get("title", "")
            if is_oa:
                # 1. Tìm url_for_pdf từ best_oa_location
                best = data.get("best_oa_location") or {}
                pdf_url = best.get("url_for_pdf")
                landing_url = best.get("url_for_landing_page") or best.get("url")

                # 2. Nếu best không có url_for_pdf, tìm trong toàn bộ oa_locations
                if not pdf_url:
                    for loc in data.get("oa_locations", []):
                        if loc.get("url_for_pdf"):
                            pdf_url = loc.get("url_for_pdf")
                            if not landing_url:
                                landing_url = loc.get("url_for_landing_page") or loc.get("url")
                            break

                # 3. Nếu tìm thấy link PDF trực tiếp
                if pdf_url:
                    return {
                        "found": True,
                        "has_pdf": True,
                        "url": pdf_url,
                        "landing_url": landing_url,
                        "title": title,
                        "source": "Open Access"
                    }

                # 4. Nếu là Open Access nhưng chỉ có trang đích (landing page)
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

def check_scihub(doi: str) -> Optional[str]:
    """Tìm bản full text miễn phí trên các mirror Sci-Hub."""
    mirrors = [
        "https://sci-hub.se",
        "https://sci-hub.st",
        "https://sci-hub.ru"
    ]
    for mirror in mirrors:
        try:
            target = f"{mirror}/{doi}"
            r = requests.get(target, headers=HEADERS, timeout=8)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                
                # Check for embed/iframe tag
                embed = soup.find("embed", id="pdf") or soup.find("iframe", id="pdf")
                if embed and embed.get("src"):
                    src = embed["src"]
                    if src.startswith("//"):
                        return "https:" + src
                    elif src.startswith("/"):
                        return mirror + src
                    return src

                # Check for download button onclick
                btn = soup.find("button", onclick=re.compile(r"location\.href='([^']+)'"))
                if btn:
                    m = re.search(r"location\.href='([^']+)'", btn["onclick"])
                    if m:
                        src = m.group(1)
                        if src.startswith("//"):
                            return "https:" + src
                        elif src.startswith("/"):
                            return mirror + src
                        return src
        except Exception:
            continue
    return None

def download_file_direct(url: str, output_path: Path) -> Tuple[bool, str]:
    """Tải trực tiếp file PDF từ URL với đa cơ chế (Requests + fallback curl)."""
    if not url or not url.startswith("http"):
        return False, "URL tải không hợp lệ."

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Cơ chế 1: Thử tải bằng requests
    try:
        r = requests.get(url, headers=HEADERS, timeout=25, allow_redirects=True, stream=True)
        if r.status_code == 200:
            content = r.content
            if content.startswith(b"%PDF") or (len(content) > 3000 and not content.strip().startswith(b"<!DOC") and not content.strip().startswith(b"<htm")):
                with open(output_path, "wb") as f:
                    f.write(content)
                return True, "Tải thành công."
    except Exception as e:
        print(f"Requests download warning for {url}: {e}")

    # Cơ chế 2: Fallback qua curl.exe (vượt qua Fastly / CDN Bot Management)
    try:
        import subprocess
        temp_out = output_path.with_suffix(".tmp")
        cmd = [
            "curl.exe", "-L", "-s", "-k",
            "-o", str(temp_out),
            "-A", HEADERS["User-Agent"],
            "-H", "Accept: application/pdf,text/html,*/*",
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

    return False, "Trang web chặn tải tự động (Cloudflare/Akamai/403). Bạn vui lòng dùng nút 'Mở link tải' để tải trực tiếp trên trình duyệt."

async def auto_download_vnu(doi: str, output_filename: str) -> Tuple[bool, str]:
    """Tự động đăng nhập qua VNU OpenAthens bằng Playwright và tải bài báo."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False, "Playwright chưa được cài đặt hoàn tất."

    output_path = settings.DOWNLOAD_FOLDER / output_filename
    vnu_url = f"{settings.VNU_OPENATHENS_BASE_URL}https://doi.org/{doi}"

    try:
        async with async_playwright() as p:
            # Mở trình duyệt Chromium (headless=False để người dùng quan sát được quá trình tự động nếu cần)
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                accept_downloads=True,
                user_agent=HEADERS["User-Agent"]
            )
            page = await context.new_page()

            # Điều hướng tới OpenAthens VNU
            await page.goto(vnu_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2000)

            # Kiểm tra nếu xuất hiện trang đăng nhập OpenAthens / IDP của VNU
            current_url = page.url.lower()
            if "openathens" in current_url or "idp." in current_url or "login" in current_url:
                username_input = await page.query_selector('input[type="text"], input[name*="user"], input[id*="user"]')
                if username_input:
                    await username_input.fill(settings.VNU_LIBRARY_ID)
                
                password_input = await page.query_selector('input[type="password"], input[name*="pass"], input[id*="pass"]')
                if password_input:
                    await password_input.fill(settings.VNU_LIBRARY_PASSWORD)
                
                submit_button = await page.query_selector('button[type="submit"], input[type="submit"], button:has-text("Đăng nhập"), button:has-text("Login")')
                if submit_button:
                    await submit_button.click()
                else:
                    await page.keyboard.press("Enter")
                
                # Đợi chuyển trang sau khi đăng nhập
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)

            # Khi đã chuyển tới trang nhà xuất bản (IEEE, Elsevier, Springer...)
            # Tìm nút PDF hoặc link tải PDF
            pdf_link = None
            
            # 1. Tìm các link thẻ a trỏ tới pdf
            links = await page.query_selector_all('a[href*=".pdf"], a[data-article-pdf], a:has-text("Download PDF"), a:has-text("PDF")')
            for link in links:
                href = await link.get_attribute("href")
                if href and ("pdf" in href.lower() or "download" in href.lower()):
                    if href.startswith("/"):
                        from urllib.parse import urljoin
                        pdf_link = urljoin(page.url, href)
                    elif href.startswith("http"):
                        pdf_link = href
                    break

            # Thử click download nếu có nút download sự kiện
            if not pdf_link:
                btn = await page.query_selector('a:has-text("Download PDF"), button:has-text("Download PDF"), a:has-text("PDF")')
                if btn:
                    try:
                        async with page.expect_download(timeout=15000) as download_info:
                            await btn.click()
                        download = await download_info.value
                        await download.save_as(str(output_path))
                        await browser.close()
                        return True, f"Đã tự động tải thành công qua VNU: {output_path.name}"
                    except Exception:
                        pass

            # Nếu lấy được URL PDF trực tiếp từ cookie session của VNU
            if pdf_link:
                cookies = await context.cookies()
                session = requests.Session()
                for c in cookies:
                    session.cookies.set(c['name'], c['value'])
                
                r = session.get(pdf_link, headers=HEADERS, timeout=30)
                if r.status_code == 200 and len(r.content) > 5000:
                    with open(output_path, "wb") as f:
                        f.write(r.content)
                    await browser.close()
                    return True, f"Đã tự động tải thành công qua VNU: {output_path.name}"

            await browser.close()
            return False, "Đăng nhập thành công nhưng chưa trích xuất được file PDF tự động từ giao diện của nhà xuất bản này."

    except Exception as e:
        return False, f"Lỗi quá trình tự động: {str(e)}"
