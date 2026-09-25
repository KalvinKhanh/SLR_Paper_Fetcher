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
from fastapi import FastAPI, File, UploadFile, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import pandas as pd
from config import settings

app = FastAPI(title="SLR Paper Fetcher")

# Check if templates directory exists, if not, create it
os.makedirs("templates", exist_ok=True)
templates = Jinja2Templates(directory="templates")

from downloader_engine import extract_doi, check_unpaywall, check_scihub, download_file_direct, auto_download_vnu

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.post("/api/process")
async def process_dois(dois: str = Form(None), file: UploadFile = File(None)):
    results = []
    items_to_process = []
    
    if file:
        content = await file.read()
        try:
            if file.filename.endswith('.csv'):
                df = pd.read_csv(io.BytesIO(content))
            elif file.filename.endswith(('.xls', '.xlsx')):
                df = pd.read_excel(io.BytesIO(content))
            else:
                return JSONResponse({"error": "Định dạng file không hỗ trợ. Vui lòng dùng file .xlsx, .xls hoặc .csv."})
            
            # Identify columns
            doi_col = None
            filename_col = None
            title_col = None
            oa_col = None
            
            for col in df.columns:
                col_str = str(col).lower()
                if not doi_col and ('doi' in col_str or 'link doi' in col_str):
                    doi_col = col
                elif not filename_col and ('tên file' in col_str or 'filename' in col_str or 'file_name' in col_str):
                    filename_col = col
                elif not title_col and ('title' in col_str or 'tiêu đề' in col_str or 'tên bài' in col_str):
                    title_col = col
                elif not oa_col and ('link oa' in col_str or 'oa' in col_str or 'open access' in col_str):
                    oa_col = col

            if not doi_col:
                for col in df.columns:
                    sample = df[col].dropna().astype(str).head(10).tolist()
                    if any('10.' in s for s in sample):
                        doi_col = col
                        break

            if not doi_col:
                return JSONResponse({"error": "Không tìm thấy cột chứa mã DOI trong file Excel/CSV."})

            for _, row in df.iterrows():
                raw_doi = str(row[doi_col]) if pd.notna(row[doi_col]) else ""
                custom_name = str(row[filename_col]) if filename_col and pd.notna(row[filename_col]) else ""
                custom_title = str(row[title_col]) if title_col and pd.notna(row[title_col]) else ""
                existing_oa = str(row[oa_col]) if oa_col and pd.notna(row[oa_col]) else ""

                if raw_doi.strip() and raw_doi.strip().lower() != 'nan':
                    items_to_process.append({
                        "raw": raw_doi,
                        "custom_name": custom_name,
                        "custom_title": custom_title,
                        "existing_oa": existing_oa
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

    for item in items_to_process:
        raw_text = item["raw"]
        doi = extract_doi(raw_text)
        custom_name = item["custom_name"]
        
        if not custom_name:
            if doi and doi.startswith("10."):
                safe_doi = re.sub(r'[^a-zA-Z0-9]', '_', doi)
                custom_name = f"{safe_doi}.pdf"
            else:
                custom_name = "paper.pdf"
        elif not custom_name.endswith('.pdf'):
            custom_name += '.pdf'

        if not doi or not doi.startswith("10."):
            results.append({
                "original": raw_text,
                "custom_name": custom_name,
                "title": item["custom_title"] or raw_text,
                "status": "Sai định dạng",
                "oa_link": None,
                "vnu_link": None
            })
            continue
        
        # 1. Check existing OA from Excel
        oa_link = None
        oa_landing = None
        is_oa = False
        source = None

        if item.get("existing_oa") and item["existing_oa"].startswith("http"):
            oa_link = item["existing_oa"]
            title = item["custom_title"] or "Tài liệu Open Access"
            is_oa = True
            source = "Excel Data"
        else:
            # 2. Check Unpaywall OA
            oa_info = check_unpaywall(doi)
            title = item["custom_title"] or oa_info.get("title") or "Unknown Title"
            if oa_info.get("found"):
                if oa_info.get("has_pdf"):
                    is_oa = True
                    oa_link = oa_info.get("url")
                    oa_landing = oa_info.get("landing_url")
                    source = "Unpaywall"
                else:
                    oa_landing = oa_info.get("landing_url")
                    source = "Unpaywall"

            # 3. Check Sci-Hub if No direct PDF link from Unpaywall
            if not oa_link:
                sh_link = check_scihub(doi)
                if sh_link:
                    is_oa = True
                    oa_link = sh_link
                    source = "Sci-Hub"

        vnu_link = f"{settings.VNU_OPENATHENS_BASE_URL}https://doi.org/{doi}"
        
        # Phân loại trạng thái
        if oa_link:
            status = "Open Access"
        elif oa_landing:
            status = "Open Access (Web)"
        else:
            status = "Paywalled"

        results.append({
            "original": raw_text,
            "doi": doi,
            "custom_name": custom_name,
            "title": title,
            "status": status,
            "oa_link": oa_link,
            "oa_landing": oa_landing,
            "source": source,
            "vnu_link": vnu_link
        })
        
    return {"results": results}

@app.post("/api/download")
async def download_file(url: str = Form(...), filename: str = Form(...)):
    target_path = settings.DOWNLOAD_FOLDER / filename
    success, msg = download_file_direct(url, target_path)
    if success:
        return {"success": True, "path": str(target_path), "message": msg}
    return JSONResponse({"success": False, "error": msg})

@app.post("/api/auto-vnu")
async def auto_vnu_endpoint(doi: str = Form(...), filename: str = Form(...)):
    success, msg = await auto_download_vnu(doi, filename)
    if success:
        return {"success": True, "message": msg}
    return JSONResponse({"success": False, "error": msg})

@app.get("/api/open-folder")
async def open_download_folder():
    folder_str = str(settings.DOWNLOAD_FOLDER.resolve())
    if os.name == 'nt':
        os.startfile(folder_str)
    return {"success": True, "folder": folder_str}

def run_server():
    uvicorn.run("app:app", host=settings.APP_HOST, port=settings.APP_PORT, reload=True, log_level="warning")

if __name__ == "__main__":
    url = f"http://{settings.APP_HOST}:{settings.APP_PORT}"
    print(f"Starting backend server at {url}")
    if settings.AUTO_OPEN_BROWSER:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    run_server()
