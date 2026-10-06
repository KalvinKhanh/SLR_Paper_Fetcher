"""Official ScienceDirect search and article PDF retrieval."""

import os
import re
import time
from pathlib import Path
from threading import Lock
from urllib.parse import quote

import requests

from browser_pdf import is_complete_pdf
from config import settings

_request_lock = Lock()
_next_request = 0.0
_cooldown_until = 0.0
_rejected_api_resources = set()


class ElsevierClient:
    def __init__(self, api_key=None, inst_token=None):
        if api_key is None:
            settings.refresh_api_credentials()
        self.api_key = settings.ELSEVIER_API_KEY if api_key is None else api_key
        self.inst_token = settings.ELSEVIER_INST_TOKEN if inst_token is None else inst_token

    def _get(self, path, accept, params=None, stream=False):
        global _next_request, _cooldown_until
        if not self.api_key:
            return None, "Chưa cấu hình ELSEVIER_API_KEY."
        headers = {"Accept": accept, "X-ELS-APIKey": self.api_key}
        if self.inst_token:
            headers["X-ELS-Insttoken"] = self.inst_token
        resource = "/content/article" if path.startswith("/content/article/") else path
        # Keys stay in headers, never in URLs, browser code, or error messages.
        with _request_lock:
            now = time.monotonic()
            if (self.api_key, self.inst_token, resource) in _rejected_api_resources:
                return None, "API Elsevier đã từ chối xác thực/quyền cho endpoint này; đã chuyển nguồn."
            if now < _cooldown_until:
                return None, "API Elsevier đang tạm nghỉ do giới hạn truy cập."
            delay = _next_request - now
            if delay > 0:
                time.sleep(delay)
            _next_request = time.monotonic() + 1.0
            try:
                response = requests.get(
                    "https://api.elsevier.com" + path,
                    headers=headers, params=params, stream=stream,
                    timeout=(8, 30), allow_redirects=False,
                )
            except requests.RequestException:
                return None, "Không kết nối được API Elsevier."
            if response.status_code == 429:
                try:
                    pause = max(1, float(response.headers.get("Retry-After", "60")))
                except ValueError:
                    pause = 60
                _cooldown_until = time.monotonic() + pause
            elif response.status_code == 401:
                # One API can be unauthorized while another remains usable.
                # A changed key/token is a new credential context.
                _rejected_api_resources.add((self.api_key, self.inst_token, resource))
        return response, ""

    @staticmethod
    def _error(status):
        if status == 401:
            return "API Elsevier từ chối xác thực/quyền endpoint (HTTP 401); kiểm tra quyền API của key."
        if status == 403:
            return "API Elsevier chưa cấp quyền nội dung này cho key/IP/token hiện tại."
        if status == 429:
            return "API Elsevier đạt giới hạn truy cập; đã tạm nghỉ và chuyển nguồn."
        if status == 404:
            return "API Elsevier không tìm thấy bài báo."
        if status == 410:
            return "Endpoint API Elsevier này đã bị ngừng; kiểm tra phiên bản tích hợp."
        return f"API Elsevier trả HTTP {status}."

    def search_doi(self, doi):
        # Escape Boolean syntax, and verify DOI instead of taking the first hit.
        query_doi = doi.replace("\\", "\\\\").replace('"', '\\"')
        response, error = self._get(
            "/content/metadata/article", "application/json",
            {"query": f'DOI("{query_doi}")', "count": 5},
        )
        if response is None:
            return {"found": False, "message": error}
        try:
            if response.status_code != 200:
                return {"found": False, "message": self._error(response.status_code)}
            entries = response.json().get("search-results", {}).get("entry", [])
            if isinstance(entries, dict):
                entries = [entries]
            for entry in entries:
                if (entry.get("prism:doi") or "").strip().lower() != doi.lower():
                    continue
                pii = entry.get("pii") or entry.get("dc:identifier", "").removeprefix("PII:")
                if not re.fullmatch(r"[A-Za-z0-9]+", pii or ""):
                    pii = None
                return {"found": True, "doi": doi, "pii": pii,
                        "title": entry.get("dc:title") or "", "message": ""}
            return {"found": False, "message": "ScienceDirect Query API chưa có kết quả đúng DOI."}
        except (ValueError, AttributeError, TypeError):
            return {"found": False, "message": "Phản hồi Query API không hợp lệ."}
        finally:
            response.close()

    def download_pdf(self, doi, output_path, progress_callback=None):
        if not self.api_key:
            return False, "Chưa cấu hình ELSEVIER_API_KEY."
        path = "/content/article/doi/" + quote(doi, safe="")
        # DOI retrieval avoids an unnecessary metadata request on every article.
        success, message, status = self._download(path, output_path, progress_callback)
        if success or status != 404:
            return success, message
        metadata = self.search_doi(doi)
        if metadata.get("found") and metadata.get("pii"):
            success, message, _ = self._download(
                "/content/article/pii/" + quote(metadata["pii"], safe=""),
                output_path, progress_callback,
            )
            return success, message
        return False, message

    def _download(self, path, output_path, progress_callback):
        response, error = self._get(path, "application/pdf", stream=True)
        if response is None:
            return False, error, None
        output_path = Path(output_path)
        partial = output_path.with_suffix(output_path.suffix + ".part")
        try:
            if response.status_code != 200:
                return False, self._error(response.status_code), response.status_code
            total = int(response.headers.get("content-length") or 0)
            received = 0
            last_percent = -1
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with partial.open("wb") as target:
                for chunk in response.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    target.write(chunk)
                    received += len(chunk)
                    percent = min(99, int(received * 100 / total)) if total else None
                    if progress_callback and percent != last_percent:
                        progress_callback(percent)
                        last_percent = percent
            with partial.open("rb") as target:
                header = target.read(5)
                target.seek(max(0, received - 4096))
                tail = target.read()
            if not is_complete_pdf(header + tail):
                return False, "API Elsevier chưa trả PDF đầy đủ; đã chuyển nguồn.", 200
            os.replace(partial, output_path)
            if progress_callback:
                progress_callback(100)
            return True, f"Đã tải PDF qua Elsevier Article Retrieval API: {output_path.name}", 200
        except (requests.RequestException, OSError, ValueError):
            return False, "Không lưu được PDF đầy đủ từ API Elsevier.", response.status_code
        finally:
            response.close()
            partial.unlink(missing_ok=True)
