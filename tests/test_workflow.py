import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from config import settings
from paper_io import normalize_doi, download_path, valid_pdf_file, public_url, provider_for_url
from paper_store import PaperStore, get_store

PDF = b"%PDF-1.4\n" + b"x" * 5000 + b"\n%%EOF\n"
DOI = "10.1016/j.example.2026.123456"


class InputTests(unittest.TestCase):
    def test_focus_signal_is_handled_on_browser_thread(self):
        from threading import Event
        from browser_verification import wait_for_browser_verification

        page = Mock()
        page.is_closed.return_value = False
        focus = Event()
        focus.set()
        with patch("browser_verification.verification_state", return_value="loading"):
            self.assertFalse(wait_for_browser_verification(page, timeout_seconds=0, focus_event=focus))
        page.bring_to_front.assert_called_once_with()
        self.assertFalse(focus.is_set())

    def test_wrapped_doi_normalizes_without_losing_punctuation(self):
        expected = "10.1000/test_(one)"
        for value in ("DOI: 10.1000/Test_(One)", " https://doi.org/10.1000/Test_%28One%29?utm_source=x#part "):
            self.assertEqual(normalize_doi(value), expected)

    def test_invalid_doi_is_rejected(self):
        for value in ("10.bad/x", "", "garbage 10.1000/abc", "10.1000/a b", "https://fake.org/10.1000/abc", "10.1000/"):
            self.assertEqual(normalize_doi(value), "")

    def test_file_paths_cannot_leave_downloads(self):
        for value in ("../README.md", "..\\README.md", "C:\\secret.pdf", "/file.pdf", "x\ny.pdf"):
            with self.assertRaises(ValueError):
                download_path(Path.cwd() / "downloads", value)

    def test_urls_do_not_retain_login_or_signed_tokens(self):
        self.assertEqual(public_url("https://user:secret@example.com/a.pdf?token=private#hash"), "https://example.com/a.pdf")
        self.assertEqual(public_url("https://example.com:broken/a"), "")
        self.assertEqual(public_url("https://example.com/article?uri=journal-123&token=private"), "https://example.com/article?uri=journal-123")

    def test_real_host_matches_provider_not_a_substring(self):
        self.assertEqual(provider_for_url("https://www.sciencedirect.com/science/article/1"), "Elsevier")
        self.assertEqual(provider_for_url("https://sciencedirect.com.fake.org/1"), "Generic")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="downloads")
        self.root = Path(self.temp.name).resolve()
        self.patches = [patch.object(settings, "DOWNLOAD_FOLDER", self.root),
                        patch.object(settings, "DB_PATH", self.root / "catalog.sqlite3"),
                        patch("app.resolve_doi", return_value={"resolved_url": "https://example.com/article", "hostname": "example.com", "provider": "Generic"}),
                        patch("app.find_paper_fulltext", return_value={"title": "Article", "status": "Paywalled"})]
        for item in self.patches:
            item.start()
        import app
        self.client = TestClient(app.app)

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def events(self, response):
        self.assertEqual(response.status_code, 200)
        return [json.loads(line) for line in response.text.splitlines()]

    def test_scan_deduplicates_and_preserves_invalid_rows(self):
        response = self.client.post("/api/process", data={"dois": DOI + "\nhttps://doi.org/" + DOI.upper() + "\n10.bad/x"})
        events = self.events(response)
        self.assertEqual(events[0]["total"], 2)
        self.assertEqual(events[0]["duplicates"], 1)
        self.assertEqual(events[-1]["results"][1]["status"], "INVALID_DOI")
        self.assertEqual(events[-1]["results"][0]["doi"], DOI)

    def test_csv_and_excel_inputs_are_supported(self):
        import io
        import pandas as pd
        table = pd.DataFrame({"DOI": [DOI], "filename": ["article"]})
        for extension in ("csv", "xlsx"):
            data = io.BytesIO()
            if extension == "csv":
                data.write(table.to_csv(index=False).encode())
            else:
                table.to_excel(data, index=False)
            response = self.client.post("/api/process", files={"file": ("papers." + extension, data.getvalue())})
            self.assertEqual(self.events(response)[-1]["results"][0]["custom_name"], "article.pdf")

    def test_health_and_ris_path_validation(self):
        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok"})
        self.assertEqual(self.client.post("/api/download", data={"url": "https://example.com/file.pdf", "filename": "file.pdf"}).status_code, 400)
        self.assertEqual(self.client.get("/api/download-ris", params={"filename": "../README.md"}).status_code, 400)
        response = self.client.post("/api/export-ris", json={"items": [{"title": "Article"}], "filename": "../bad"})
        self.assertEqual(response.status_code, 400)

    def test_focus_verification_requests_live_browser_tab(self):
        with patch("app.browser_worker.request_focus_verification", return_value=True) as focus:
            response = self.client.post("/api/focus-verification")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True})
        focus.assert_called_once_with()
        with patch("app.browser_worker.request_focus_verification", return_value=False):
            self.assertEqual(self.client.post("/api/focus-verification").status_code, 409)

    def test_malformed_batch_is_rejected_before_streaming(self):
        for items in ([1], [{"doi": "10.bad/x"}], [{"doi": DOI, "filename": "../bad.pdf"}], [{"doi": DOI}, {"doi": DOI}]):
            self.assertEqual(self.client.post("/api/download-all", json={"items": items}).status_code, 400)

    def test_batch_counters_include_existing_and_failed_rows(self):
        async def fake_download(doi, filename, title, progress_callback, status_callback, oa_url):
            if doi == DOI:
                progress_callback(100)
                return True, "Existing", "existing"
            status_callback("public_sources_finished")
            status_callback("checking_vnu")
            status_callback("public_fallback_started")
            return False, "NO_ACCESS", "unavailable"
        with patch("app.auto_download_paper", side_effect=fake_download):
            events = self.events(self.client.post("/api/download-all", json={"items": [
                {"doi": DOI, "filename": "one.pdf"}, {"doi": "10.1000/two", "filename": "two.pdf"}]}))
        final = events[-1]
        self.assertEqual((final["success_count"], final["fail_count"]), (1, 1))
        progress = [event for event in events if event["type"] == "progress"][-1]
        self.assertEqual((progress["oa_done"], progress["oa_total"], progress["vnu_done"], progress["vnu_total"]), (3, 3, 1, 1))
        self.assertTrue(any(event["type"] == "lane_add" and event["lane"] == "oa" for event in events))

    def test_deleted_pdf_is_not_reported_as_downloaded(self):
        path = self.root / "missing.pdf"
        get_store().update(DOI, "DOWNLOADED", filename=path.name, file_path=str(path))
        result = self.client.get("/api/papers", params={"doi": DOI}).json()["paper"]
        self.assertEqual(result["status"], "INVALID_PDF")

    def test_manual_sync_requires_exact_source_instead_of_latest_file(self):
        response = self.client.post("/api/sync-recent-download", data={"filename": "one.pdf"})
        self.assertEqual(response.status_code, 422)
        from downloader_engine import import_verified_pdf
        source, target = self.root / "source.pdf", self.root / "target.pdf"
        source.write_bytes(PDF)
        self.assertTrue(import_verified_pdf(DOI, source, target)[0])
        self.assertEqual(source.read_bytes(), PDF)
        self.assertEqual(target.read_bytes(), PDF)
        self.assertFalse(import_verified_pdf("10.1000/other", source, target)[0])
        self.assertEqual(get_store().file_owner(target), DOI)

    def test_catalog_redacts_urls_and_detects_partial_pdf(self):
        path = self.root / "file.pdf"
        path.write_bytes(PDF)
        self.assertTrue(valid_pdf_file(path))
        store = get_store()
        store.update(DOI, "DOWNLOADED", filename=path.name, file_path=str(path),
                     resolved_url="https://example.com/pdf?token=secret", message="URL='https://example.com/pdf?token=secret'")
        self.assertEqual(store.file_owner(path), DOI)
        self.assertTrue(store.downloaded(DOI))
        self.assertNotIn("secret", json.dumps(store.get(DOI)))
        path.write_bytes(PDF[:-20])
        self.assertFalse(store.downloaded(DOI))


if __name__ == "__main__":
    unittest.main()
