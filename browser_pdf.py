"""Save PDFs observed in an authenticated browser, including PDF viewer tabs."""

import os
import base64
from pathlib import Path
from urllib.parse import urlparse
from paper_io import provider_for_url


def is_complete_pdf(body):
    # A range starting at zero can have a PDF header while still being incomplete.
    return len(body) >= 32 and b"%PDF-" in body[:1024] and not body.lstrip().startswith(b"<") and b"%%EOF" in body[-4096:]


class BrowserPdfCapture:
    def __init__(self, context, output_path, progress_callback=None, tracked_pages=None):
        self.context = context
        self.output_path = Path(output_path)
        self.progress_callback = progress_callback
        self.urls = []
        self.responses = []
        self.downloads = []
        self.attempted_urls = set()
        self.page_handlers = []
        self.existing_pages = set(context.pages) - set(tracked_pages or [])
        self.request_guard = None
        self.browser_only = False
        # Register before creating pages: popup navigation can start before its
        # Page event, and the initial PDF response must not be missed.
        context.on("request", self._on_request)
        context.on("response", self._on_response)
        context.on("requestfinished", self._on_finished)
        context.on("page", self._on_page)
        for page in tracked_pages or []:
            self._on_page(page)

    @staticmethod
    def _is_pdf_url(url):
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return parsed.scheme in ("https", "http") and (
            host == "pdf.sciencedirectassets.com"
            or parsed.path.lower().endswith(".pdf")
        )

    def _remember_url(self, url):
        # Keep the original signed URL intact, including all query parameters.
        if url not in self.urls:
            self.urls.append(url)

    def _on_request(self, request):
        try:
            if request.frame.page in self.existing_pages:
                return
        except Exception:
            return
        if request.method == "GET" and self._is_pdf_url(request.url):
            self._remember_url(request.url)

    def _on_response(self, response):
        try:
            if response.request.frame.page in self.existing_pages:
                return
        except Exception:
            return
        content_type = response.headers.get("content-type", "").lower()
        if response.status in (200, 206) and (
            "application/pdf" in content_type or self._is_pdf_url(response.url)
            or ".pdf" in response.headers.get("content-disposition", "").lower()
        ):
            self._remember_url(response.url)

    def _on_finished(self, request):
        try:
            if request.frame.page in self.existing_pages:
                return
        except Exception:
            return
        response = request.response()
        if response and response.status == 200 and response.url in self.urls:
            self.responses.append(response)

    def _on_page(self, page):
        handler = lambda download: self.downloads.append(download)
        self.page_handlers.append((page, handler))
        page.on("download", handler)

    def close(self):
        for event, handler in (("request", self._on_request), ("response", self._on_response),
                               ("requestfinished", self._on_finished), ("page", self._on_page)):
            self.context.remove_listener(event, handler)
        for page, handler in self.page_handlers:
            page.remove_listener("download", handler)
        self.page_handlers.clear()

    def save_body(self, body):
        if not is_complete_pdf(body):
            return False
        partial = self.output_path.with_suffix(self.output_path.suffix + ".part")
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            partial.write_bytes(body)
            os.replace(partial, self.output_path)
        finally:
            partial.unlink(missing_ok=True)
        if self.progress_callback:
            self.progress_callback(100)
        return True

    def save_download(self, download):
        partial = self.output_path.with_suffix(self.output_path.suffix + ".part")
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            download.save_as(str(partial))
            body = partial.read_bytes()
            partial.unlink(missing_ok=True)
            return self.save_body(body)
        except Exception:
            return False
        finally:
            partial.unlink(missing_ok=True)

    def save(self, referer=""):
        if self.request_guard:
            self.request_guard()
        downloads, self.downloads = self.downloads, []
        for download in downloads:
            if self.save_download(download):
                return True
        responses, self.responses = self.responses, []
        for response in reversed(responses):
            try:
                if self.save_body(response.body()):
                    return True
            except Exception:
                continue
        for page in self.context.pages:
            if page in self.existing_pages:
                continue
            if self._is_pdf_url(page.url):
                self._remember_url(page.url)
        for url in list(reversed(self.urls))[:4]:
            if url in self.attempted_urls:
                continue
            self.attempted_urls.add(url)
            if self.request_guard:
                self.request_guard()
            if self.save_url(url, referer):
                return True
        return self._save_blob_pdf()

    def save_url(self, url, referer=""):
        if self.request_guard:
            self.request_guard()
        if self.browser_only or provider_for_url(url) == "Elsevier":
            return self._save_browser_url(url, referer)
        response = None
        try:
            # Public/non-challenged publishers may use context cookies here.
            response = self.context.request.get(
                url, headers={"Accept": "application/pdf,*/*", "Referer": referer}, timeout=25000)
            return response.status == 200 and self.save_body(response.body())
        except Exception:
            return False
        finally:
            if response:
                response.dispose()

    def _save_browser_url(self, url, referer):
        """Fetch only after verification, using the actual browser network stack."""
        pages = [page for page in self.context.pages if page not in self.existing_pages and not page.is_closed()
                 and not page.url.startswith(("chrome://", "edge://"))]
        target_host = urlparse(url).hostname
        referer_host = urlparse(referer).hostname
        pages.sort(key=lambda page: urlparse(page.url).hostname not in (target_host, referer_host))
        for page in pages[:2]:
            if self.request_guard:
                self.request_guard()
            try:
                encoded = page.evaluate("""async url => {
                    const response = await fetch(url, {credentials: 'include', signal: AbortSignal.timeout(20000)});
                    if (response.status !== 200 || Number(response.headers.get('content-length')) > 128 * 1024 * 1024) return null;
                    const type = response.headers.get('content-type') || '';
                    if (type.includes('text/html')) return null;
                    const bytes = new Uint8Array(await response.arrayBuffer());
                    if (bytes.length > 128 * 1024 * 1024) return null;
                    let binary = '';
                    for (let i = 0; i < bytes.length; i += 16384)
                        binary += String.fromCharCode(...bytes.subarray(i, i + 16384));
                    return btoa(binary);
                }""", url)
                if encoded and self.save_body(base64.b64decode(encoded, validate=True)):
                    return True
            except Exception:
                continue
        return False

    def _save_blob_pdf(self):
        """Read a PDF already exposed to the authorized page as a blob URL."""
        pages = [page for page in self.context.pages if page not in self.existing_pages and not page.is_closed()]
        blob_urls = [page.url for page in pages if page.url.startswith("blob:")]
        for page in pages:
            for frame in page.frames[:6]:
                if self.request_guard:
                    self.request_guard()
                try:
                    urls = frame.evaluate("""() => Array.from(document.querySelectorAll('iframe[src],embed[src],object[data],a[href]'))
                        .map(el => el.src || el.data || el.href).filter(url => url && url.startsWith('blob:'))""")
                    for url in list(dict.fromkeys(blob_urls + urls))[:4]:
                        if url in self.attempted_urls:
                            continue
                        # The creator frame can access a blob opened in the
                        # browser's native viewer; no viewer toolbar is needed.
                        encoded = frame.evaluate("""async url => {
                            const response = await fetch(url);
                            const bytes = new Uint8Array(await response.arrayBuffer());
                            if (bytes.length > 128 * 1024 * 1024) return null;
                            let binary = '';
                            for (let i = 0; i < bytes.length; i += 16384)
                                binary += String.fromCharCode(...bytes.subarray(i, i + 16384));
                            return btoa(binary);
                        }""", url)
                        if encoded and self.save_body(base64.b64decode(encoded, validate=True)):
                            self.attempted_urls.add(url)
                            return True
                except Exception:
                    continue
        return False
