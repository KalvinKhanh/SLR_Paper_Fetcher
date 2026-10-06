import sys
import asyncio

import os
import re
import io
import json
import webbrowser
import threading
import subprocess
import uvicorn
import requests
from typing import List, Optional
from urllib.parse import urlparse
from fastapi import FastAPI, File, UploadFile, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import pandas as pd
from config import settings
from config import BASE_DIR
from contextlib import asynccontextmanager
from paper_io import download_path, safe_filename, valid_pdf_file, safe_message, public_url, institutional_url
from paper_store import get_store
from doi_resolver import resolve_doi
from browser_session import browser_manager, browser_worker

@asynccontextmanager
async def lifespan(app):
    get_store()
    try:
        await browser_manager.start()
        if settings.AUTO_OPEN_BROWSER:
            async def open_ui():
                import urllib.request
                url = f"http://{settings.APP_HOST}:{settings.APP_PORT}"
                health_url = f"{url}/api/health"
                ready = False
                for _ in range(30):
                    await asyncio.sleep(0.5)
                    try:
                        def check_health():
                            with urllib.request.urlopen(health_url, timeout=1) as resp:
                                return resp.status == 200
                        ready = await asyncio.to_thread(check_health)
                        if ready:
                            break
                    except Exception:
                        continue
                if ready and browser_manager.anchor and not browser_manager.anchor.is_closed():
                    try:
                        print(f"[UI] Opening web interface in primary browser window: {url}")
                        await browser_manager.run(browser_manager.anchor.goto, url)
                        await browser_manager.run(browser_manager.anchor.bring_to_front)
                    except Exception:
                        pass
            asyncio.create_task(open_ui())
    except Exception as e:
        print(f"[BROWSER] Warning: Could not start browser at startup: {e}")
    yield
    browser_manager.request_shutdown()
    if _DOWNLOAD_JOBS:
        await asyncio.gather(*list(_DOWNLOAD_JOBS), return_exceptions=True)
    from downloader_engine import _PIPELINE_JOBS
    if _PIPELINE_JOBS:
        await asyncio.gather(*list(_PIPELINE_JOBS), return_exceptions=True)
    await browser_manager.shutdown()

app = FastAPI(title="SLR Paper Fetcher", lifespan=lifespan)

def _publisher_url_from_message(message: str) -> Optional[str]:
    match = re.search(r"URL='([^']+)'", message or "")
    if not match:
        return None
    candidate = match.group(1).strip()
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return candidate

# Check if templates directory exists, if not, create it
os.makedirs("templates", exist_ok=True)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

from downloader_engine import (
    extract_doi, 
    find_paper_fulltext, 
    download_file_direct, 
    auto_download_paper,
    import_verified_pdf,
    get_paper_ris,
    generate_combined_ris
)


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={
        "vnu_configured": bool(settings.VNU_LIBRARY_ID and settings.VNU_LIBRARY_PASSWORD)})


@app.get("/api/health")
async def health():
    get_store()
    return {"status": "ok"}


@app.post("/api/focus-verification")
async def focus_verification():
    focused = await browser_manager.run(browser_manager.focus_verification_page)
    if not focused and not browser_worker.request_focus_verification():
        return JSONResponse({"error": "Không có tab xác minh đang mở."}, status_code=409)
    return {"success": True}


@app.get("/api/papers")
async def paper_status(doi: Optional[str] = None):
    if doi is not None:
        normalized = extract_doi(doi)
        if not normalized:
            return JSONResponse({"error": "INVALID_DOI"}, status_code=400)
        record = await asyncio.to_thread(get_store().get, normalized)
        if record and record["status"] == "DOWNLOADED" and not valid_pdf_file(record.get("file_path") or ""):
            get_store().update(normalized, "INVALID_PDF", message="PDF đã bị xóa hoặc không còn đầy đủ.")
            record = get_store().get(normalized)
        return {"paper": record}
    papers = await asyncio.to_thread(get_store().list, 2000)
    for record in papers:
        if record["status"] == "DOWNLOADED" and not valid_pdf_file(record.get("file_path") or ""):
            get_store().update(record["doi"], "INVALID_PDF", message="PDF đã bị xóa hoặc không còn đầy đủ.")
            record["status"] = "INVALID_PDF"
    return {"papers": papers}

@app.post("/api/process")
async def process_dois(dois: str = Form(None), file: UploadFile = File(None)):
    results = []
    items_to_process = []
    
    if file:
        content = await file.read()
        try:
            if (file.filename or '').lower().endswith('.csv'):
                df = pd.read_csv(io.BytesIO(content))
            elif (file.filename or '').lower().endswith(('.xls', '.xlsx')):
                df = pd.read_excel(io.BytesIO(content))
            else:
                return JSONResponse({"error": "Định dạng file không hỗ trợ. Vui lòng dùng file .xlsx, .xls hoặc .csv."})
            
            # Identify columns
            doi_col = None
            filename_col = None
            title_col = None
            oa_col = None
            journal_col = None
            author_col = None
            year_col = None
            
            for col in df.columns:
                col_str = str(col).lower()
                if doi_col is None and ('doi' in col_str or 'link doi' in col_str):
                    doi_col = col
                elif filename_col is None and ('tên file' in col_str or 'filename' in col_str or 'file_name' in col_str):
                    filename_col = col
                elif title_col is None and ('title' in col_str or 'tiêu đề' in col_str or 'tên bài' in col_str):
                    title_col = col
                elif oa_col is None and ('link oa' in col_str or 'oa' in col_str or 'open access' in col_str):
                    oa_col = col
                elif journal_col is None and any(k in col_str for k in ['journal', 'tạp chí', 'nguồn', 'source', 'publisher', 'venue']):
                    journal_col = col
                elif author_col is None and any(k in col_str for k in ['author', 'tác giả', 'authors']):
                    author_col = col
                elif year_col is None and any(k in col_str for k in ['year', 'năm', 'pub_year', 'date']):
                    year_col = col

            if doi_col is None:
                for col in df.columns:
                    sample = df[col].dropna().astype(str).head(10).tolist()
                    if any('10.' in s for s in sample):
                        doi_col = col
                        break

            if doi_col is None:
                return JSONResponse({"error": "Không tìm thấy cột chứa mã DOI trong file Excel/CSV."})

            for _, row in df.iterrows():
                raw_doi = str(row[doi_col]) if pd.notna(row[doi_col]) else ""
                custom_name = str(row[filename_col]) if filename_col and pd.notna(row[filename_col]) else ""
                custom_title = str(row[title_col]) if title_col and pd.notna(row[title_col]) else ""
                existing_oa = str(row[oa_col]) if oa_col and pd.notna(row[oa_col]) else ""
                custom_journal = str(row[journal_col]) if journal_col and pd.notna(row[journal_col]) else ""
                custom_author = str(row[author_col]) if author_col and pd.notna(row[author_col]) else ""
                custom_year = str(row[year_col]) if year_col and pd.notna(row[year_col]) else ""

                if raw_doi.strip() and raw_doi.strip().lower() != 'nan':
                    items_to_process.append({
                        "raw": raw_doi,
                        "custom_name": custom_name,
                        "custom_title": custom_title,
                        "existing_oa": existing_oa,
                        "custom_journal": custom_journal,
                        "custom_author": custom_author,
                        "custom_year": custom_year
                    })
        except Exception as e:
            return JSONResponse({"error": f"Lỗi đọc file: {str(e)}"})
    elif dois:
        for line in dois.split('\n'):
            line = line.strip()
            if line:
                items_to_process.append({
                    "raw": line,
                    "custom_name": "",
                    "custom_title": "",
                    "existing_oa": ""
                })
    
    if not items_to_process:
        return JSONResponse({"error": "Không có danh sách DOI nào được cung cấp."})

    seen = set()
    unique = []
    duplicate_count = 0
    for item in items_to_process:
        doi = extract_doi(item["raw"])
        if doi and doi in seen:
            duplicate_count += 1
            continue
        if doi:
            seen.add(doi)
        unique.append(item)
    items_to_process = unique

    async def stream_results():
        import json
        total = len(items_to_process)
        yield json.dumps({"type": "start", "total": total, "duplicates": duplicate_count, "concurrency": 6}) + "\n"
        results = [None] * total
        semaphore = asyncio.Semaphore(6)

        def process_one_item(item):
            raw_text = item["raw"]
            doi = extract_doi(raw_text)
            custom_name = item["custom_name"]
            if not custom_name:
                custom_name = f"{re.sub(r'[^a-zA-Z0-9]', '_', doi)}.pdf" if doi and doi.startswith("10.") else "paper.pdf"
            if not doi or not doi.startswith("10."):
                import hashlib
                get_store().update("invalid:" + hashlib.sha256(raw_text.encode()).hexdigest(), "INVALID_DOI",
                                   message="DOI không hợp lệ.")
                return {"original": raw_text, "custom_name": custom_name,
                        "title": item["custom_title"] or raw_text, "status": "INVALID_DOI",
                        "oa_link": None, "vnu_link": None}

            custom_name = safe_filename(custom_name)

            resolution = resolve_doi(doi)
            saved = get_store().downloaded(doi)
            if saved:
                custom_name = saved["filename"]

            oa_link = oa_landing = source = None
            status = "Paywalled"
            if item.get("existing_oa") and item["existing_oa"].startswith("http"):
                oa_link = item["existing_oa"]
                title = item["custom_title"] or "T\u00e0i li\u1ec7u Open Access"
                source, status = "Excel Data", "Open Access"
            else:
                fulltext_info = find_paper_fulltext(doi)
                title = item["custom_title"] or fulltext_info.get("title") or "Unknown Title"
                oa_link = fulltext_info.get("pdf_url")
                oa_landing = fulltext_info.get("landing_url")
                source = fulltext_info.get("source")
                status = fulltext_info.get("status") or ("Open Access" if oa_link else "Paywalled")

            record = get_store().get(doi) or {}
            persistent_status = "DOWNLOADED" if saved else ("OPEN_ACCESS" if oa_link else record.get("status", "PENDING"))
            get_store().update(doi, persistent_status, filename=custom_name,
                               metadata={"title": title, "authors": item.get("custom_author") or "",
                                         "journal": item.get("custom_journal") or source or "", "year": item.get("custom_year") or ""})

            return {
                "original": raw_text, "doi": doi, "custom_name": custom_name, "title": title,
                "journal": item.get("custom_journal") or source or "",
                "authors": item.get("custom_author") or "", "year": item.get("custom_year") or "",
                "status": status, "state": (get_store().get(doi) or {}).get("status", "PENDING"), "already_exists": bool(saved),
                "resolved_url": resolution.get("resolved_url"), "provider": resolution.get("provider"),
                "oa_link": oa_link, "oa_landing": oa_landing, "source": source,
                "vnu_link": institutional_url(settings.VNU_OPENATHENS_BASE_URL, doi)
            }

        async def process_index(index, item):
            async with semaphore:
                try:
                    result = await asyncio.to_thread(process_one_item, item)
                except Exception as exc:
                    result = {"original": item["raw"], "doi": extract_doi(item["raw"]),
                              "custom_name": item.get("custom_name") or "paper.pdf",
                              "title": item.get("custom_title") or item["raw"],
                              "status": "FAILED", "message": type(exc).__name__, "oa_link": None, "vnu_link": None}
                return index, result

        tasks = [asyncio.create_task(process_index(i, item)) for i, item in enumerate(items_to_process)]
        try:
            completed = 0
            for task in asyncio.as_completed(tasks):
                index, result = await task
                results[index] = result
                completed += 1
                yield json.dumps({"type": "progress", "completed": completed, "total": total,
                                  "index": index, "result": result}, ensure_ascii=False) + "\n"
            yield json.dumps({"type": "done", "results": results}, ensure_ascii=False) + "\n"
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

    return StreamingResponse(stream_results(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.post("/api/download")
async def download_file(url: str = Form(...), filename: str = Form(...), doi: str = Form("")):
    try:
        target_path = download_path(settings.DOWNLOAD_FOLDER, filename)
    except ValueError as error:
        return JSONResponse({"success": False, "error": str(error)}, status_code=400)
    doi = extract_doi(doi)
    if not doi:
        return JSONResponse({"success": False, "error": "Cần DOI hợp lệ để tải và theo dõi đúng bài."}, status_code=400)
    success, msg, method = await auto_download_paper(doi, target_path.name, oa_url=url)
    if success:
        record = get_store().get(doi) or {}
        return {"success": True, "path": record.get("file_path", str(target_path)), "message": msg, "method": method}
    return JSONResponse({"success": False, "error": msg})

_DOWNLOAD_JOBS = set()


@app.post("/api/download-all")
async def download_all(request: Request):
    try:
        payload = await request.json()
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items or len(items) > 2000 or any(not isinstance(item, dict) for item in items):
            raise ValueError("Invalid items")
        seen = set()
        names = set()
        for item in items:
            doi = extract_doi(item.get("doi"))
            filename = safe_filename(item.get("filename") or re.sub(r"[^a-z0-9]", "_", doi) + ".pdf")
            if not doi or doi in seen or filename.lower() in names:
                raise ValueError("Invalid DOI, duplicate DOI or duplicate filename")
            seen.add(doi)
            names.add(filename.lower())
            item.update(doi=doi, filename=filename)
    except (ValueError, TypeError):
        return JSONResponse({"error": "Danh sach DOI/ten file khong hop le hoac bi trung."}, status_code=400)

    async def stream_downloads():
        loop = asyncio.get_running_loop()
        events = asyncio.Queue()
        total = len(items)
        oa_total = total
        oa_done = vnu_done = vnu_total = completed = 0
        success_count = fail_count = 0

        async def download_one(index, item):
            lane = "oa"

            def send(event):
                loop.call_soon_threadsafe(events.put_nowait, event)

            def status(value):
                nonlocal lane
                if value == "public_sources_finished" and lane == "oa":
                    lane = "vnu"
                    send({"type": "lane_done", "lane": "oa"})
                    send({"type": "lane_add", "lane": "vnu"})
                elif value == "public_fallback_started" and lane == "vnu":
                    lane = "oa"
                    send({"type": "lane_done", "lane": "vnu"})
                    send({"type": "lane_add", "lane": "oa"})
                send({"type": "item_progress", "index": index, "lane": lane, "percent": None, "status": value})

            try:
                success, message, method = await auto_download_paper(
                    item["doi"], item["filename"], str(item.get("title") or ""),
                    progress_callback=lambda percent: send({"type": "item_progress", "index": index, "lane": lane, "percent": percent}),
                    status_callback=status, oa_url=item.get("oa_link"))
                record = get_store().get(item["doi"]) or {}
                result = {"success": success, "filename": record.get("filename") or item["filename"],
                          "method": method, "message": message, "state": record.get("status"),
                          "open_url": record.get("resolved_url") or _publisher_url_from_message(message) or "https://doi.org/" + item["doi"]}
            except Exception as error:
                result = {"success": False, "filename": item["filename"], "message": type(error).__name__, "state": "FAILED"}
            send({"type": "lane_done", "lane": lane})
            send({"type": "progress", "index": index, "result": result})

        yield json.dumps({"type": "start", "total": total, "oa_total": total, "vnu_total": 0}) + "\n"
        tasks = [asyncio.create_task(download_one(index, item)) for index, item in enumerate(items)]
        # A disconnected tab does not cancel an authenticated file write.
        for task in tasks:
            _DOWNLOAD_JOBS.add(task)
            task.add_done_callback(_DOWNLOAD_JOBS.discard)
        while completed < total:
            try:
                event = await asyncio.wait_for(events.get(), timeout=10)
            except asyncio.TimeoutError:
                yield json.dumps({"type": "heartbeat", "completed": completed, "total": total}) + "\n"
                continue
            if event["type"] == "lane_add":
                if event["lane"] == "oa":
                    oa_total += 1
                else:
                    vnu_total += 1
            elif event["type"] == "lane_done":
                if event["lane"] == "oa":
                    oa_done += 1
                else:
                    vnu_done += 1
            elif event["type"] == "progress":
                completed += 1
                if event["result"]["success"]:
                    success_count += 1
                else:
                    fail_count += 1
            event.update(completed=completed, total=total, oa_done=oa_done, oa_total=oa_total,
                         vnu_done=vnu_done, vnu_total=vnu_total, success_count=success_count, fail_count=fail_count)
            yield json.dumps(event, ensure_ascii=False) + "\n"
        yield json.dumps({"type": "done", "total": total, "success_count": success_count, "fail_count": fail_count}) + "\n"

    return StreamingResponse(stream_downloads(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/sync-recent-download")
async def sync_recent_download(filename: str = Form(...), source_filename: str = Form(...), doi: str = Form(...)):
    """Explicit import only: never assign the latest Windows download to a DOI."""
    from pathlib import Path
    doi = extract_doi(doi)
    if not doi:
        return JSONResponse({"success": False, "error": "INVALID_DOI"}, status_code=400)
    try:
        source = download_path(Path.home() / "Downloads", source_filename)
        target = download_path(settings.DOWNLOAD_FOLDER, filename)
        success, message = await asyncio.to_thread(import_verified_pdf, doi, source, target)
        return JSONResponse({"success": success, "filename": target.name, "message": message}, status_code=200 if success else 409)
    except (ValueError, OSError) as error:
        return JSONResponse({"success": False, "error": type(error).__name__}, status_code=400)

@app.post("/api/auto-vnu")
async def auto_vnu_endpoint(doi: str = Form(...), filename: str = Form(...), title: str = Form("")):
    doi = extract_doi(doi)
    if not doi.startswith("10."):
        return JSONResponse({"success": False, "error": "DOI không hợp lệ."}, status_code=400)
    try:
        filename = safe_filename(filename)
    except ValueError as error:
        return JSONResponse({"success": False, "error": str(error)}, status_code=400)
    success, msg, method = await auto_download_paper(doi, filename, title)
    if success:
        return {"success": True, "message": msg, "method": method, "filename": (get_store().get(doi) or {}).get("filename", filename)}
    record = get_store().get(doi) or {}
    return JSONResponse({"success": False, "error": msg, "state": record.get("status"), "open_url": record.get("resolved_url") or _publisher_url_from_message(msg)})


@app.get("/api/elsevier-status")
async def elsevier_status():
    settings.refresh_api_credentials()
    return {"api_key_configured": bool(settings.ELSEVIER_API_KEY),
            "institution_token_configured": bool(settings.ELSEVIER_INST_TOKEN),
            "automatic_download_enabled": False, "download_strategy": "institutional_browser",
            "manual_verification_enabled": settings.VNU_ALLOW_MANUAL_VERIFICATION}


@app.get("/api/elsevier-search")
async def elsevier_search(doi: str):
    return JSONResponse({"found": False, "message": "Đã ngừng dùng API Elsevier. Tải qua phiên đăng nhập VNU/OpenAthens."}, status_code=410)

@app.get("/api/open-folder")
async def open_download_folder():
    folder_str = str(settings.DOWNLOAD_FOLDER.resolve())
    if os.name == 'nt':
        os.startfile(folder_str)
    return {"success": True, "folder": folder_str}

@app.post("/api/export-ris")
async def export_ris_endpoint(request: Request):
    """Xuất file trích dẫn RIS (.ris) cho danh sách các bài báo."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Dữ liệu JSON không hợp lệ."})

    items = data.get("items", []) if isinstance(data, dict) else []
    if not isinstance(items, list) or not items or any(not isinstance(item, dict) for item in items):
        return JSONResponse({"success": False, "error": "Không có bài báo nào để xuất RIS."})

    try:
        filename = safe_filename(data.get("filename", "slr_citations.ris"), ".ris")
    except ValueError as error:
        return JSONResponse({"success": False, "error": str(error)}, status_code=400)

    try:
        ris_content = await generate_combined_ris(items)
        if not ris_content.strip():
            return JSONResponse({"success": False, "error": "Không tạo được nội dung RIS nào."})

        # Lưu một bản sao vào thư mục downloads của project
        target_path = download_path(settings.DOWNLOAD_FOLDER, filename, ".ris")
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(ris_content)

        return {
            "success": True,
            "filename": filename,
            "content": ris_content,
            "count": len(items),
            "saved_path": str(target_path)
        }
    except Exception as e:
        return JSONResponse({"success": False, "error": f"Lỗi tạo file RIS: {str(e)}"})

@app.get("/api/download-ris")
async def download_ris_endpoint(filename: str = "slr_citations.ris"):
    try:
        target_path = download_path(settings.DOWNLOAD_FOLDER, filename, ".ris")
    except ValueError as error:
        return JSONResponse({"success": False, "error": str(error)}, status_code=400)
    if not target_path.exists():
        return JSONResponse({"success": False, "error": "File không tồn tại."})
    return FileResponse(
        path=str(target_path),
        filename=filename,
        media_type="application/x-research-info-systems"
    )

def run_server():
    # Let shutdown reach lifespan even when a stream is awaiting a human.
    uvicorn.run("app:app", host=settings.APP_HOST, port=settings.APP_PORT, log_level="warning",
                timeout_graceful_shutdown=10)

if __name__ == "__main__":
    url = f"http://{settings.APP_HOST}:{settings.APP_PORT}"
    print(f"Starting backend server at {url}")
    # The UI is now opened directly inside the Playwright browser's anchor tab
    run_server()
