"""Real Playwright fixtures; no credentials or external publisher requests."""

import asyncio
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import patch

from jinja2 import Environment, FileSystemLoader
from browser_session import BrowserWorker
from browser_pdf import BrowserPdfCapture
from config import BASE_DIR, settings
from downloader_engine import _run_sync_vnu, auto_download_vnu
from downloader_engine import wait_for_browser_verification
from institutional_auth import requires_mfa, is_authenticated, has_fulltext_access

PDF = b"%PDF-1.4\n" + b"x" * 5000 + b"\n%%EOF\n"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, body, content_type="text/html", headers=None):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/article":
            self.reply(b'<title>Fixture publisher</title><p>Access provided by Vietnam National University</p><p>Full text access</p><a href="/pdf">Download PDF</a>')
        elif self.path == "/pdf":
            self.reply(PDF, "application/pdf", {"Content-Disposition": 'attachment; filename="fixture.pdf"'})
        elif self.path == "/range.pdf":
            if "fixture=1" not in self.headers.get("Cookie", ""):
                self.send_error(403)
            elif self.headers.get("Range"):
                self.send_response(206)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Range", f"bytes 0-999/{len(PDF)}")
                self.end_headers()
                self.wfile.write(PDF[:1000])
            else:
                self.reply(PDF, "application/pdf")
        elif self.path == "/":
            html = Environment(loader=FileSystemLoader(str(BASE_DIR / "templates"))).get_template("index.html").render(vnu_configured=True)
            self.reply(html.encode())
        else:
            self.send_error(404)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path != "/api/download-all":
            return self.send_error(404)
        events = [
            {"type": "start", "total": 1, "oa_total": 1, "vnu_total": 0},
            {"type": "item_progress", "index": 0, "lane": "oa", "percent": 50},
            {"type": "progress", "index": 0, "completed": 1, "total": 1, "oa_done": 1, "oa_total": 1,
             "vnu_done": 0, "vnu_total": 0, "result": {"success": True, "filename": "two.pdf"}},
            {"type": "done", "total": 1},
        ]
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for event in events:
            self.wfile.write((json.dumps(event) + "\n").encode())
            self.wfile.flush()
            time.sleep(0.15)


class BrowserIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="downloads")
        self.root = Path(self.temp.name).resolve()
        self.worker = BrowserWorker()
        self.patches = [patch.object(settings, "DOWNLOAD_FOLDER", self.root),
                        patch.object(settings, "VNU_BROWSER_STATE_FILE", self.root / "state.json"),
                        patch.object(settings, "VNU_BROWSER_PROFILE_DIR", self.root / "profile"),
                        patch.object(settings, "VNU_BROWSER_CHANNEL", "chromium"),
                        patch.object(settings, "DB_PATH", self.root / "papers.sqlite3"),
                        patch.object(settings, "VNU_BROWSER_HEADLESS", True),
                        patch.object(settings, "VNU_ALLOW_MANUAL_VERIFICATION", False),
                        patch.object(settings, "VNU_LIBRARY_ID", "fixture-user"),
                        patch.object(settings, "VNU_LIBRARY_PASSWORD", "fixture-password"),
                        patch("downloader_engine.browser_worker", self.worker)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        asyncio.run(self.worker.shutdown())
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_authorized_login_is_reused_for_two_pdf_downloads(self):
        logins = []
        routed_contexts = set()
        real_ensure = self.worker._ensure

        def ensure_with_fixture_routes():
            real_ensure()
            if self.worker.context in routed_contexts:
                return
            routed_contexts.add(self.worker.context)

            def route_auth(route):
                request = route.request
                if "/submit" in request.url:
                    fields = parse_qs(request.post_data or "")
                    self.assertEqual(fields, {"username": ["fixture-user"], "password": ["fixture-password"]})
                    logins.append(1)
                    route.fulfill(status=302, headers={"Location": self.base + "/article", "Set-Cookie": "fixture_login=1; Path=/; Secure; SameSite=None"})
                elif "fixture_login=1" in request.headers.get("cookie", ""):
                    route.fulfill(status=302, headers={"Location": self.base + "/article"})
                else:
                    route.fulfill(content_type="text/html", body='<form action="/submit" method="post"><input name="username" type="text"><input name="password" type="password"><button>Sign in</button></form>')

            self.worker.context.route("https://**/*", lambda route: route.abort())
            self.worker.context.route("https://sso.vnu.edu.vn/**", route_auth)

        def setup():
            self.worker._ensure()
            return self.worker.context

        async def run():
            context = await self.worker.run(setup)
            outcomes = []
            for index in (1, 2):
                outcomes.append(await self.worker.run(_run_sync_vnu, f"10.1000/{index}", f"{index}.pdf"))
            self.assertIs(self.worker.context, context)
            self.assertEqual(await self.worker.run(lambda: len(self.worker.article_pages())), 0)
            self.assertEqual(await self.worker.run(lambda: context.pages[0].url), "about:blank")
            await self.worker.run(self.worker._close)
            await self.worker.run(setup)
            outcomes.append(await auto_download_vnu("10.1000/3", "3.pdf"))
            return outcomes

        with patch.object(settings, "VNU_OPENATHENS_BASE_URL", "https://sso.vnu.edu.vn/auth?doi="), \
                patch.object(self.worker, "_ensure", side_effect=ensure_with_fixture_routes):
            outcomes = asyncio.run(run())
        self.assertTrue(all(result[0] for result in outcomes), outcomes)
        self.assertEqual(logins, [1])
        self.assertEqual((self.root / "1.pdf").read_bytes(), PDF)
        self.assertEqual((self.root / "2.pdf").read_bytes(), PDF)
        self.assertEqual((self.root / "3.pdf").read_bytes(), PDF)

    def test_capture_uses_context_cookies_and_detaches_previous_listeners(self):
        def run():
            with self.worker.session() as (_, context):
                context.add_cookies([{"name": "fixture", "value": "1", "url": self.base}])
                capture = BrowserPdfCapture(context, self.root / "range.pdf")
                capture._remember_url(self.base + "/range.pdf")
                self.assertTrue(capture.save())
                capture.close()
                other = BrowserPdfCapture(context, self.root / "other.pdf")
                page = context.new_page()
                with page.expect_download() as info:
                    try:
                        page.goto(self.base + "/pdf")
                    except Exception as error:
                        if "ERR_ABORTED" not in str(error) and "Download is starting" not in str(error):
                            raise
                self.assertTrue(other.save_download(info.value))
                self.assertEqual(capture.downloads, [])
                other.close()
                blob = BrowserPdfCapture(context, self.root / "blob.pdf")
                creator = context.new_page()
                creator.goto(self.base + "/article")
                creator.evaluate("""bytes => {
                    const url = URL.createObjectURL(new Blob([new Uint8Array(bytes)], {type: 'application/pdf'}));
                    const link = document.createElement('a');
                    link.href = url;
                    document.body.append(link);
                }""", list(PDF))
                self.assertTrue(blob.save())
                blob.close()
        asyncio.run(self.worker.run(run))
        self.assertEqual((self.root / "range.pdf").read_bytes(), PDF)
        self.assertEqual((self.root / "blob.pdf").read_bytes(), PDF)

    def test_ui_escapes_metadata_and_maps_filtered_batch_progress(self):
        def run():
            with self.worker.session() as (_, context):
                context.route("https://**/*", lambda route: route.abort())
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(self.base, wait_until="domcontentloaded")
                page.evaluate("""() => {
                    currentResults = [{doi:'10.1000/one', custom_name:'one.pdf', already_exists:true, title:'First'},
                        {doi:'10.1000/two', custom_name:'two.pdf', title:'<img src=x onerror=window.injected=true>'}];
                    displayResults(currentResults);
                    window.progressRows = [];
                    new MutationObserver(() => {
                        for (const id of ['action-cell-0', 'action-cell-1']) {
                            if (document.getElementById(id).textContent.includes('50%')) window.progressRows.push(id);
                        }
                    }).observe(document.getElementById('resultsBody'), {subtree:true, childList:true});
                }""")
                self.assertEqual(page.locator("#resultsBody img").count(), 0)
                page.locator("#action-cell-1 button").click()
                page.wait_for_function("document.getElementById('state-cell-1').dataset.state === 'DOWNLOADED'")
                self.assertEqual(page.locator("#action-cell-0 button").count(), 1)
                self.assertEqual(page.evaluate("window.progressRows"), ["action-cell-1"])
                self.assertFalse(page.evaluate("Boolean(window.injected)"))
                self.assertEqual(errors, [])
        asyncio.run(self.worker.run(run))

    def test_verification_wait_resumes_without_clicking_the_challenge(self):
        def run():
            with self.worker.session() as (_, context):
                page = context.new_page()
                page.set_content('<title>Just a moment...</title><button onclick="window.clicked=true">Verify you are human</button>')
                # A brief apparent success followed by another challenge must
                # not release automation. This is a local fixture, not a solver.
                page.evaluate("""() => {
                    setTimeout(() => {document.title='Article'; document.body.innerHTML='Full text access to the publisher article and its PDF.';}, 200);
                    setTimeout(() => {document.title='Just a moment...'; document.body.innerHTML='Verify you are human';}, 500);
                    setTimeout(() => {document.title='Article'; document.body.innerHTML='Full text access to the publisher article and its PDF.'; window.finalArticle=true;}, 900);
                }""")
                statuses = []
                self.assertTrue(wait_for_browser_verification(page, 7, statuses.append))
                self.assertEqual(statuses, ["waiting_for_verification", "verification_complete"])
                self.assertTrue(page.evaluate("Boolean(window.finalArticle)"))
                self.assertIs(page.context, context)
                self.assertFalse(page.evaluate("Boolean(window.clicked)"))
                page.close()
                self.assertFalse(wait_for_browser_verification(page, 0))

            requests = []
            user_agents = []
            article_url = "https://www.sciencedirect.com/science/article/pii/FIXTURE"
            def publisher(route):
                requests.append(route.request.url)
                user_agents.append(route.request.headers.get("user-agent", ""))
                if route.request.url.endswith("/pdf"):
                    route.fulfill(body=PDF, content_type="application/pdf",
                                  headers={"Content-Disposition": 'attachment; filename="fixture.pdf"'})
                elif "fixture_verified=1" in route.request.headers.get("cookie", ""):
                    route.fulfill(content_type="text/html", body='<title>Article</title><p>Full text access to the publisher article and its PDF.</p><a href="/pdf">Download PDF</a>')
                else:
                    route.fulfill(content_type="text/html", body='<title>Just a moment...</title><button onclick="window.clicked=true">Verify you are human</button>')
            context.route("https://**/*", lambda route: route.abort())
            context.route("https://www.sciencedirect.com/**", publisher)
            doi = "10.1000/manual-verification"
            from paper_store import get_store
            get_store().update(doi, "PROCESSING", provider="Elsevier", hostname="www.sciencedirect.com")
            with patch.object(settings, "VNU_OPENATHENS_BASE_URL", article_url + "?doi="):
                first = _run_sync_vnu(doi, "verified.pdf")
                self.assertFalse(first[0])
                self.assertTrue(first[1].startswith("CAPTCHA_REQUIRED:"))
                challenged = self.worker.pending_verifications[doi]
                self.assertFalse(challenged.is_closed())
                self.assertIs(self.worker.context, context)
                before = len(requests)
                # Simulate the user completing the fixture's verification.
                challenged.evaluate("""() => {
                    document.cookie = 'fixture_verified=1; Path=/';
                    sessionStorage.setItem('fixture_session', 'preserved');
                    document.title='Article';
                    document.body.innerHTML='<p>Full text access to the publisher article and its PDF.</p><a href="/pdf">Download PDF</a>';
                }""")
                second = _run_sync_vnu(doi, "verified.pdf")
                self.assertTrue(second[0], second)
                self.assertEqual(len([url for url in requests[before:] if '/science/article/' in url]), 0)
                self.assertIs(self.worker.context, context)
                self.assertFalse(challenged.is_closed())
                self.assertEqual(challenged.evaluate("sessionStorage.getItem('fixture_session')"), "preserved")
                self.assertFalse(challenged.evaluate("Boolean(window.clicked)"))
                self.assertIs(self.worker.reusable_page("10.1000/next", "Elsevier")[0], challenged)
                next_doi = "10.1000/next"
                get_store().update(next_doi, "PROCESSING", provider="Elsevier", hostname="www.sciencedirect.com")
                third = _run_sync_vnu(next_doi, "next.pdf")
                self.assertTrue(third[0], third)
                self.assertIs(self.worker.context, context)
                self.assertIs(self.worker.verified_pages["Elsevier"], challenged)
                self.assertEqual(len(set(user_agents)), 1)
                self.assertTrue(user_agents[0])
                self.assertEqual(challenged.evaluate("sessionStorage.getItem('fixture_session')"), "preserved")
                with patch.object(context, "close") as close:
                    self.assertFalse(self.worker.recover_after_close())
                    close.assert_not_called()
                challenged.set_content('<title>Just a moment...</title><p>Verify you are human</p>')
                stop = threading.Event()
                timer = threading.Timer(0.4, stop.set)
                timer.start()
                try:
                    self.assertFalse(wait_for_browser_verification(challenged, None, stop_event=stop))
                finally:
                    timer.cancel()
                self.assertFalse(challenged.is_closed())
        asyncio.run(self.worker.run(run))
        self.assertEqual((self.root / "verified.pdf").read_bytes(), PDF)
        self.assertEqual((self.root / "next.pdf").read_bytes(), PDF)

    def test_mfa_and_login_page_do_not_count_as_authenticated(self):
        def run():
            with self.worker.session() as (_, context):
                context.route("https://sso.vnu.edu.vn/**", lambda route: route.fulfill(content_type="text/html", body='<input autocomplete="one-time-code"><p>Vietnam National University</p>'))
                page = context.new_page()
                page.goto("https://sso.vnu.edu.vn/auth")
                self.assertTrue(requires_mfa(page))
                self.assertFalse(is_authenticated(page))
                self.assertFalse(has_fulltext_access(page))
        asyncio.run(self.worker.run(run))


if __name__ == "__main__":
    unittest.main()
