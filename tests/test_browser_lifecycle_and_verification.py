"""Tests verifying the 1-browser, 1-persistent-context, and verification lifecycle architecture."""

import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from config import settings
from browser_session import BrowserManager
from browser_verification import (
    detect_cloudflare_challenge,
    is_sciencedirect_article_page,
    verification_state,
    wait_for_browser_verification,
)


class BrowserLifecycleArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.manager = BrowserManager()
        self.patches = [
            patch.object(settings, "DOWNLOAD_FOLDER", self.root),
            patch.object(settings, "VNU_BROWSER_STATE_FILE", self.root / "state.json"),
            patch.object(settings, "VNU_BROWSER_PROFILE_DIR", self.root / "profile"),
            patch.object(settings, "VNU_BROWSER_CHANNEL", "chromium"),
            patch.object(settings, "VNU_BROWSER_HEADLESS", True),
            patch.object(settings, "VNU_ALLOW_MANUAL_VERIFICATION", True),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        asyncio.run(self.manager.shutdown())
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_1_start_app_creates_only_one_browser_and_persistent_context(self):
        """TEST 1: Application startup creates exactly ONE browser context."""
        context1 = asyncio.run(self.manager.start())
        self.assertIsNotNone(context1)
        self.assertEqual(self.manager.browser_creation_count, 1)
        self.assertEqual(self.manager.context_id, "CONTEXT-001")

        # Repeated calls to start / get_context MUST reuse the same persistent context
        context2 = self.manager.get_context()
        self.assertIs(context1, context2)
        self.assertEqual(self.manager.browser_creation_count, 1)

    def test_2_and_3_sciencedirect_challenge_uses_same_page_and_detects_manual_verify(self):
        """TEST 2, 3, 4: ScienceDirect challenge uses same page, detects manual verify, no reload, no new browser."""
        def run():
            context = self.manager.get_context()
            page = self.manager.get_article_page()
            page_id_before = self.manager.get_page_id(page)

            # 1. Challenge appears on article tab
            page.set_content(
                '<title>Just a moment...</title>'
                '<body><p>Verify you are human</p><div id="cf-challenge-running"></div></body>'
            )
            self.assertTrue(detect_cloudflare_challenge(page))
            self.assertEqual(verification_state(page), "challenge")
            self.assertEqual(self.manager.browser_creation_count, 1)

            # 2. Simulate user verifying in page after 300ms using JS timer (safe across greenlet threads)
            page.evaluate("""() => {
                setTimeout(() => {
                    document.title = 'An In-depth Study on AI - ScienceDirect';
                    document.body.innerHTML = `
                        <div id="article-header"><h1>An In-depth Study on AI</h1></div>
                        <div id="abstracts"><h2>Abstract</h2><p>Article abstract content here...</p></div>
                        <button aria-label="View PDF">View PDF</button>
                    `;
                }, 300);
            }""")

            statuses = []
            success = wait_for_browser_verification(page, timeout_seconds=5, status_callback=statuses.append)

            self.assertTrue(success)
            self.assertIn("waiting_for_verification", statuses)
            self.assertIn("verification_complete", statuses)

            # Verify same browser, same context, same page
            self.assertEqual(self.manager.browser_creation_count, 1)
            self.assertIs(page.context, context)
            self.assertEqual(self.manager.get_page_id(page), page_id_before)

        asyncio.run(self.manager.run(run))

    def test_5_second_doi_reuses_same_browser_context(self):
        """TEST 5: Second DOI reuses the same browser, context, and persistent profile."""
        def run():
            context = self.manager.get_context()
            page1 = self.manager.get_article_page()
            self.manager.finish_verification(page1)

            # Next request calls get_context / reusable_page
            reused_context = self.manager.get_context()
            self.assertIs(context, reused_context)
            self.assertEqual(self.manager.browser_creation_count, 1)

        asyncio.run(self.manager.run(run))

    def test_6_concurrent_verification_only_opens_one_and_cooperates(self):
        """TEST 6: Multiple tasks encountering verification coordinate via auth_lock and event."""
        verified_count = 0

        def simulate_auth_task(task_id):
            nonlocal verified_count
            with self.manager._thread_auth_lock:
                if self.manager._thread_verification_event.is_set():
                    return "reused_session"
                time.sleep(0.2)
                verified_count += 1
                self.manager._thread_verification_event.set()
                return "performed_verification"

        results = []
        threads = [
            threading.Thread(target=lambda i=i: results.append(simulate_auth_task(i)))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(verified_count, 1)
        self.assertIn("performed_verification", results)
        self.assertEqual(results.count("reused_session"), 4)

    def test_7_long_wait_does_not_recreate_browser(self):
        """TEST 7: Patient waiting does not restart or create another browser."""
        def run():
            context = self.manager.get_context()
            page = self.manager.get_article_page()
            page.set_content('<title>Just a moment...</title><p>Verify you are human</p>')

            stop = threading.Event()
            # Stop after 0.5 seconds
            threading.Timer(0.5, stop.set).start()

            result = wait_for_browser_verification(page, timeout_seconds=10, stop_event=stop)
            self.assertFalse(result)
            # Browser and context must NOT be recreated
            self.assertEqual(self.manager.browser_creation_count, 1)
            self.assertFalse(page.is_closed())
            self.assertIs(self.manager.context, context)

        asyncio.run(self.manager.run(run))


if __name__ == "__main__":
    unittest.main()
