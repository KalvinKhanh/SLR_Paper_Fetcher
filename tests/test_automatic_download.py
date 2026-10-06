import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import elsevier_client
from elsevier_client import ElsevierClient
from config import settings
from downloader_engine import auto_download_paper, download_file_direct

PDF = b"%PDF-1.4\n" + b"x" * 9000 + b"\n%%EOF\n"
DOI = "10.1016/j.example.2026.123456"


class PublicDownloadTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir="downloads")
        self.output = Path(self.folder.name) / "public.pdf"

    def tearDown(self):
        self.folder.cleanup()

    def test_public_pdf_uses_cloudscraper_and_closes_session(self):
        response = Response(body=PDF)
        scraper = Mock()
        scraper.headers = {"User-Agent": "fixture-browser"}
        scraper.get.return_value = response
        progress = []
        with patch("downloader_engine.cloudscraper.create_scraper", return_value=scraper), \
             patch("http_policy.requests.get") as plain_get:
            success, message = download_file_direct(
                "https://public-fixture.invalid/paper.pdf", self.output,
                lambda percent, received, total: progress.append(percent),
            )
        self.assertTrue(success, message)
        self.assertEqual(self.output.read_bytes(), PDF)
        self.assertEqual(progress[-1], 100)
        self.assertEqual(scraper.get.call_args.kwargs["headers"]["User-Agent"], "fixture-browser")
        self.assertTrue(scraper.get.call_args.kwargs["stream"])
        self.assertTrue(response.closed)
        scraper.close.assert_called_once()
        plain_get.assert_not_called()

    def test_public_429_preserves_retry_after_cooldown(self):
        import http_policy

        response = Response(status=429, headers={"Retry-After": "120"})
        scraper = Mock()
        scraper.headers = {"User-Agent": "fixture-browser"}
        scraper.get.return_value = response
        with patch.object(http_policy, "_cooldowns", {}), \
             patch("downloader_engine.cloudscraper.create_scraper", return_value=scraper):
            first = download_file_direct("https://cooldown-fixture.invalid/paper.pdf", self.output)
            second = download_file_direct("https://cooldown-fixture.invalid/paper.pdf", self.output)
        self.assertFalse(first[0])
        self.assertIn("429", first[1])
        self.assertFalse(second[0])
        self.assertEqual(scraper.get.call_count, 1)
        self.assertFalse(self.output.exists())
        self.assertTrue(response.closed)


class Response:
    def __init__(self, status=200, body=PDF, data=None, headers=None):
        self.status_code = status
        self.body = body
        self.data = data
        self.headers = headers if headers is not None else {"content-length": str(len(body))}
        self.closed = False

    def json(self):
        return self.data

    def iter_content(self, chunk_size):
        yield self.body[:4000]
        yield self.body[4000:]

    def close(self):
        self.closed = True


class ElsevierTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir="downloads")
        self.output = Path(self.folder.name) / "paper.pdf"
        self.client = ElsevierClient(api_key="fixture-key", inst_token="fixture-token")
        self.patches = [
            patch.object(elsevier_client, "_next_request", 0.0),
            patch.object(elsevier_client, "_cooldown_until", 0.0),
            patch.object(elsevier_client, "_rejected_api_resources", set()),
            patch.object(elsevier_client.time, "sleep"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.folder.cleanup()

    def test_pdf_auth_headers_and_completion(self):
        progress = []
        response = Response()
        with patch.object(elsevier_client.requests, "get", return_value=response) as request:
            success, message = self.client.download_pdf(DOI, self.output, progress.append)
        self.assertTrue(success, message)
        self.assertEqual(self.output.read_bytes(), PDF)
        self.assertEqual(progress[-1], 100)
        self.assertNotIn(100, progress[:-1])
        self.assertFalse(self.output.with_suffix(".pdf.part").exists())
        call = request.call_args
        self.assertNotIn("fixture-key", call.args[0])
        self.assertNotIn("fixture-token", message)
        self.assertEqual(call.kwargs["headers"]["X-ELS-APIKey"], "fixture-key")
        self.assertEqual(call.kwargs["headers"]["X-ELS-Insttoken"], "fixture-token")
        self.assertFalse(call.kwargs["allow_redirects"])
        self.assertTrue(response.closed)

    def test_missing_key_does_not_make_requests(self):
        with patch.object(elsevier_client.requests, "get") as request:
            success, _ = ElsevierClient(api_key="").download_pdf(DOI, self.output)
        self.assertFalse(success)
        request.assert_not_called()

    def test_query_verifies_exact_doi(self):
        response = Response(data={"search-results": {"entry": [
            {"prism:doi": "10.1016/wrong", "pii": "S0000000000000000"},
            {"prism:doi": DOI.upper(), "pii": "S1234567890123456", "dc:title": "Correct article"},
        ]}})
        with patch.object(elsevier_client.requests, "get", return_value=response) as request:
            result = self.client.search_doi(DOI)
        self.assertTrue(result["found"])
        self.assertEqual(result["pii"], "S1234567890123456")
        self.assertEqual(request.call_args.kwargs["params"]["query"], f'DOI("{DOI}")')

    def test_doi_404_resolves_pii_using_query_api(self):
        query = Response(data={"search-results": {"entry": [
            {"prism:doi": DOI, "pii": "S1234567890123456"},
        ]}})
        with patch.object(elsevier_client.requests, "get", side_effect=[Response(404), query, Response()]) as request:
            success, _ = self.client.download_pdf(DOI, self.output)
        self.assertTrue(success)
        self.assertIn("/content/metadata/article", request.call_args_list[1].args[0])
        self.assertTrue(request.call_args_list[2].args[0].endswith("/pii/S1234567890123456"))

    def test_403_does_not_repeat_or_save_error_page(self):
        with patch.object(elsevier_client.requests, "get", return_value=Response(403)) as request:
            success, message = self.client.download_pdf(DOI, self.output)
        self.assertFalse(success)
        self.assertIn("chưa cấp quyền", message)
        self.assertEqual(request.call_count, 1)
        self.assertFalse(self.output.exists())

    def test_401_disables_rejected_key_for_remaining_queue(self):
        with patch.object(elsevier_client.requests, "get", return_value=Response(401)) as request:
            self.client.download_pdf(DOI, self.output)
            self.client.download_pdf(DOI + "2", self.output)
        self.assertEqual(request.call_count, 1)

    def test_429_obeys_retry_after_across_articles(self):
        with patch.object(elsevier_client.requests, "get", return_value=Response(429, headers={"Retry-After": "120"})) as request:
            self.client.download_pdf(DOI, self.output)
            success, message = self.client.download_pdf(DOI + "2", self.output)
        self.assertFalse(success)
        self.assertIn("tạm nghỉ", message)
        self.assertEqual(request.call_count, 1)

    def test_metadata_401_does_not_disable_article_retrieval(self):
        with patch.object(elsevier_client.requests, "get", side_effect=[Response(401), Response()]) as request:
            metadata = self.client.search_doi(DOI)
            success, _ = self.client.download_pdf(DOI, self.output)
        self.assertFalse(metadata["found"])
        self.assertTrue(success)
        self.assertEqual(request.call_count, 2)

    def test_changed_institution_token_can_retry_authorization(self):
        with patch.object(elsevier_client.requests, "get", side_effect=[Response(401), Response()]) as request:
            self.client.download_pdf(DOI, self.output)
            updated = ElsevierClient(api_key="fixture-key", inst_token="new-fixture-token")
            success, _ = updated.download_pdf(DOI, self.output)
        self.assertTrue(success)
        self.assertEqual(request.call_count, 2)

    def test_html_and_partial_pdf_never_report_completion(self):
        for body in (b"<html>access denied</html>", PDF[:4000]):
            with self.subTest(body=body[:10]):
                progress = []
                with patch.object(elsevier_client.requests, "get", return_value=Response(body=body)):
                    success, _ = self.client.download_pdf(DOI, self.output, progress.append)
                self.assertFalse(success)
                self.assertNotIn(100, progress)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.output.with_suffix(".pdf.part").exists())


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir="downloads")
        self.root = Path(self.folder.name).resolve()
        self.patches = [patch.object(settings, "DOWNLOAD_FOLDER", self.root),
                        patch.object(settings, "DB_PATH", self.root / "papers.sqlite3"),
                        patch.object(settings, "VNU_LIBRARY_ID", ""),
                        patch.object(settings, "VNU_LIBRARY_PASSWORD", ""),
                        patch("doi_resolver.resolve_doi", return_value={"provider": "Elsevier", "hostname": "www.sciencedirect.com"})]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.folder.cleanup()

    async def save_institution_pdf(self, doi, filename, *args):
        (self.root / filename).write_bytes(PDF)
        return True, "Institution PDF"

    def save_public_pdf(self, doi, title, path, *args):
        path.write_bytes(PDF)
        return True, "University PDF"

    def test_institution_download_never_calls_elsevier_api_or_public_search(self):
        with patch.object(settings, "VNU_LIBRARY_ID", "fixture-user"), \
             patch.object(settings, "VNU_LIBRARY_PASSWORD", "fixture-password"), \
             patch.object(ElsevierClient, "download_pdf") as api, \
             patch("automatic_sources.download_public_copy", return_value=(False, "No public PDF")) as public, \
             patch("downloader_engine.auto_download_vnu", side_effect=self.save_institution_pdf) as browser:
            result = asyncio.run(auto_download_paper(DOI, "fixture.pdf"))
        self.assertEqual(result, (True, "Institution PDF", "vnu"))
        public.assert_not_called()
        api.assert_not_called()
        browser.assert_called_once()

    def test_public_copy_does_not_need_vnu_or_manual_verification(self):
        with patch.object(ElsevierClient, "download_pdf", return_value=(False, "Missing API key")), \
             patch("automatic_sources.download_public_copy", side_effect=self.save_public_pdf), \
             patch("downloader_engine.auto_download_vnu", new_callable=AsyncMock) as browser:
            result = asyncio.run(auto_download_paper(DOI, "fixture.pdf"))
        self.assertEqual(result[2], "public_copy")
        self.assertTrue(result[0])
        browser.assert_not_called()

    def test_success_without_a_pdf_is_not_marked_downloaded(self):
        from paper_store import get_store
        with patch("automatic_sources.download_public_copy", return_value=(True, "PDF")):
            result = asyncio.run(auto_download_paper(DOI, "fixture.pdf"))
        self.assertFalse(result[0])
        self.assertEqual(get_store().get(DOI)["status"], "INVALID_PDF")

    def test_resume_does_not_fetch_existing_valid_pdf(self):
        from paper_store import get_store
        with patch("automatic_sources.download_public_copy", side_effect=self.save_public_pdf):
            asyncio.run(auto_download_paper(DOI, "fixture.pdf"))
        with patch("automatic_sources.download_public_copy") as public:
            result = asyncio.run(auto_download_paper(DOI, "different-name.pdf"))
        self.assertTrue(result[0])
        self.assertEqual(result[2], "existing")
        self.assertEqual(get_store().get(DOI)["filename"], "fixture.pdf")
        public.assert_not_called()

    def test_resume_after_process_stopped_between_save_and_commit(self):
        from paper_store import get_store
        path = self.root / "fixture.pdf"
        path.write_bytes(PDF)
        get_store().update(DOI, "PROCESSING", filename=path.name, file_path=str(path))
        with patch("automatic_sources.download_public_copy") as public:
            result = asyncio.run(auto_download_paper(DOI, path.name))
        self.assertTrue(result[0])
        self.assertEqual(get_store().get(DOI)["status"], "DOWNLOADED")
        public.assert_not_called()

    def test_two_dois_cannot_claim_the_same_file(self):
        from paper_store import get_store
        get_store().update("10.1000/another", "PROCESSING", filename="fixture.pdf", file_path=str(self.root / "fixture.pdf"))
        with patch("automatic_sources.download_public_copy") as public:
            result = asyncio.run(auto_download_paper(DOI, "fixture.pdf"))
        self.assertFalse(result[0])
        public.assert_not_called()

    def test_public_download_concurrency_is_bounded(self):
        import threading
        import time
        guard = threading.Lock()
        active = maximum = 0
        public_finished = []
        browser_finished = []

        def public_copy(doi, title, path, *args):
            nonlocal active, maximum
            if doi == "10.1000/0":
                return False, "No public copy"
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.1)
            path.write_bytes(PDF)
            with guard:
                active -= 1
                public_finished.append(time.monotonic())
            return True, "Public PDF"

        async def browser_copy(doi, filename, *args):
            await asyncio.sleep(0.7)
            (self.root / filename).write_bytes(PDF)
            browser_finished.append(time.monotonic())
            return True, "Institution PDF"

        async def run():
            return await asyncio.gather(*(auto_download_paper(f"10.1000/{index}", f"{index}.pdf", oa_url="https://fixture.invalid/pdf") for index in range(6)))

        def quick_copy(url, path, callback):
            return public_copy("10.1000/" + path.stem, "", path)

        with patch.object(settings, "VNU_LIBRARY_ID", "fixture-user"), \
             patch.object(settings, "VNU_LIBRARY_PASSWORD", "fixture-password"), \
             patch("downloader_engine.download_file_direct", side_effect=quick_copy), \
             patch("automatic_sources.download_public_copy", side_effect=public_copy), \
             patch.object(ElsevierClient, "download_pdf", return_value=(False, "No API access")), \
             patch("downloader_engine.auto_download_vnu", side_effect=browser_copy):
            results = asyncio.run(run())
        self.assertTrue(all(result[0] for result in results))
        self.assertEqual(maximum, 2)
        self.assertLess(max(public_finished), browser_finished[0])

    def test_resolved_provider_overrides_elsevier_prefix(self):
        with patch("doi_resolver.resolve_doi", return_value={"provider": "IEEE", "hostname": "ieeexplore.ieee.org"}), \
             patch.object(ElsevierClient, "download_pdf") as api, \
             patch("automatic_sources.download_public_copy", return_value=(False, "No public copy")), \
             patch("downloader_engine.auto_download_vnu", new_callable=AsyncMock, return_value=(False, "No access")):
            result = asyncio.run(auto_download_paper(DOI, "fixture.pdf"))
        self.assertFalse(result[0])
        api.assert_not_called()

    def test_publisher_cooldown_skips_repeated_captcha_attempts(self):
        import time
        with patch.object(ElsevierClient, "download_pdf", return_value=(False, "Missing API key")), \
             patch("automatic_sources.download_public_copy", return_value=(False, "No public copy")), \
             patch("downloader_engine._publisher_cooldowns", {"www.sciencedirect.com": time.monotonic() + 600}), \
             patch("downloader_engine.auto_download_vnu", new_callable=AsyncMock) as browser:
            result = asyncio.run(auto_download_paper(DOI, "fixture.pdf"))
        self.assertFalse(result[0])
        self.assertIn("không lặp lại xác minh", result[1])
        browser.assert_not_called()
        with patch("doi_resolver.resolve_doi", return_value={"provider": "Elsevier", "hostname": "linkinghub.elsevier.com"}), \
             patch.object(ElsevierClient, "download_pdf", return_value=(False, "Missing API key")), \
             patch("automatic_sources.download_public_copy", return_value=(False, "No public copy")), \
             patch("downloader_engine._publisher_cooldowns", {"provider:Elsevier": time.monotonic() + 600}), \
             patch("downloader_engine.auto_download_vnu", new_callable=AsyncMock) as browser:
            result = asyncio.run(auto_download_paper(DOI, "fixture.pdf"))
        self.assertFalse(result[0])
        browser.assert_not_called()


class CredentialRefreshTests(unittest.TestCase):
    def test_env_edits_and_removal_apply_without_retaining_old_key(self):
        import config
        original = {field: getattr(settings, field) for field in config._API_FIELDS}
        try:
            with tempfile.TemporaryDirectory(dir="downloads") as folder:
                env_path = Path(folder) / "fixture.env"
                with patch.object(config, "ENV_PATH", env_path), \
                     patch.object(config, "_API_ENV_DEFAULTS", {field: "" for field in config._API_FIELDS}):
                    env_path.write_text('ELSEVIER_API_KEY=" fixture-one "\n', encoding="utf-8")
                    self.assertEqual(ElsevierClient().api_key, "fixture-one")
                    env_path.write_text('ELSEVIER_API_KEY=fixture-two\nELSEVIER_INST_TOKEN=fixture-token\n', encoding="utf-8")
                    client = ElsevierClient()
                    self.assertEqual(client.api_key, "fixture-two")
                    self.assertEqual(client.inst_token, "fixture-token")
                    env_path.write_text('', encoding="utf-8")
                    self.assertEqual(ElsevierClient().api_key, "")
        finally:
            for field, value in original.items():
                setattr(settings, field, value)


if __name__ == "__main__":
    unittest.main()
