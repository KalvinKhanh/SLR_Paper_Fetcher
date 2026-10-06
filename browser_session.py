"""Own Playwright on one thread; reuse the authorized context between papers."""

import asyncio
import json
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Lock
from urllib.parse import urlparse

from config import settings
from institutional_auth import TRUSTED_LOGIN_DOMAINS
from paper_io import provider_for_url


class BrowserManager:
    """Singleton service managing the single persistent browser process and context."""

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vnu-browser")
        self.playwright = self.browser = self.context = None
        self.context_id = None
        self.browser_creation_count = 0
        self.page_counter = 0

        self.current_article_page = None
        self.auth_page = None

        # Lock and Event for cross-DOI authentication coordination
        self.auth_lock = asyncio.Lock()
        self.verification_event = asyncio.Event()
        self._thread_auth_lock = Lock()
        self._thread_verification_event = Event()

        self.authenticated = False
        self.anchor = None
        self.channel = None
        self.headless = True
        self.retained_pages = set()
        self.pending_verifications = {}
        self.pending_providers = {}
        self.verified_pages = {}
        self.challenge_session = False
        self.stopping = Event()
        self.focus_requested = Event()

    def get_page_id(self, page):
        """Get or assign a persistent debug runtime ID to a page."""
        if not page:
            return "PAGE-NONE"
        pid = getattr(page, "_runtime_page_id", None)
        if not pid:
            self.page_counter += 1
            pid = f"PAGE-{self.page_counter:03d}"
            try:
                setattr(page, "_runtime_page_id", pid)
            except Exception:
                pass
        return pid

    async def start(self):
        """Start BrowserManager and create the single persistent context at application startup."""
        print("[BROWSER]\nStarting BrowserManager")
        return await self.run(self._ensure)

    def get_context(self):
        """Return the persistent context, reusing the existing one if alive."""
        if self.context and self.browser and self.browser.is_connected():
            print(f"[BROWSER]\nContext reused\ncontext_id={self.context_id}")
            return self.context
        return self._ensure()

    def get_article_page(self):
        """Return current article page or create a new tab in the same persistent context."""
        context = self.get_context()
        if self.current_article_page and not self.current_article_page.is_closed():
            pid = self.get_page_id(self.current_article_page)
            print(f"[ARTICLE]\nContinuing SAME page\npage_id={pid}")
            return self.current_article_page

        self.current_article_page = context.new_page()
        pid = self.get_page_id(self.current_article_page)
        print(f"[PAGE]\nArticle page created\npage_id={pid}")
        return self.current_article_page

    def get_auth_page(self):
        """Get or reuse the authentication tab in the SAME context. Does not launch a browser."""
        context = self.get_context()
        if self.auth_page and not self.auth_page.is_closed():
            try:
                self.auth_page.bring_to_front()
            except Exception:
                pass
            return self.auth_page

        self.auth_page = context.new_page()
        pid = self.get_page_id(self.auth_page)
        print(f"[PAGE]\nAuth page created\npage_id={pid}")
        return self.auth_page

    def focus_verification_page(self):
        """Bring the active verification page, auth page, or article page to front."""
        if self.pending_verifications:
            for page in list(self.pending_verifications.values()):
                if page and not page.is_closed():
                    try:
                        page.bring_to_front()
                        return True
                    except Exception:
                        pass
        if self.auth_page and not self.auth_page.is_closed():
            try:
                self.auth_page.bring_to_front()
                return True
            except Exception:
                pass
        if self.current_article_page and not self.current_article_page.is_closed():
            try:
                self.current_article_page.bring_to_front()
                return True
            except Exception:
                pass
        return False

    def pin_verification(self, page, doi, provider=None):
        self.challenge_session = True
        self.retained_pages.add(page)
        self.pending_verifications[doi] = page
        actual_provider = provider_for_url(page.url)
        self.pending_providers[doi] = actual_provider if actual_provider != "Generic" else (provider or "Generic")
        
        # Clear verification event when a challenge begins
        self._thread_verification_event.clear()
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(self.verification_event.clear)
        except RuntimeError:
            pass

        if not self.headless:
            try:
                page.bring_to_front()
            except Exception:
                pass

    def request_focus_verification(self):
        """Ask the owning Playwright thread to show the live challenge tab."""
        if self.headless or not self.pending_verifications:
            return False
        self.focus_requested.set()
        return True

    def finish_verification(self, page):
        for doi, pending in list(self.pending_verifications.items()):
            if pending is page:
                del self.pending_verifications[doi]
                self.pending_providers.pop(doi, None)
        if not self.pending_verifications:
            self.focus_requested.clear()
        provider = provider_for_url(page.url)
        if provider != "Generic" and not urlparse(page.url).path.lower().endswith(".pdf"):
            self.verified_pages[provider] = page

        # Signal completion to any waiting DOI tasks
        self._thread_verification_event.set()
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(self.verification_event.set)
        except RuntimeError:
            pass

    def reusable_page(self, doi, provider):
        pending = self.pending_verifications.get(doi)
        if pending and not pending.is_closed():
            return pending, True
        verified = self.verified_pages.get(provider)
        if verified and not verified.is_closed() and verified not in self.pending_verifications.values():
            return verified, False
        if self.current_article_page and not self.current_article_page.is_closed() and self.current_article_page not in self.pending_verifications.values():
            return self.current_article_page, False
        return None, False

    def pending_publisher_page(self, provider):
        return next((page for doi, page in self.pending_verifications.items()
                     if not page.is_closed() and provider and provider != "Generic"
                     and self.pending_providers.get(doi) == provider), None)

    async def run(self, function, *args):
        return await asyncio.get_running_loop().run_in_executor(self.executor, function, *args)

    def article_pages(self):
        # Edge creates a hidden downloads-hub target after saving an attachment.
        # It is browser UI, not an article tab, and cannot be closed with Page.close.
        if not self.context:
            return []
        return [page for page in self.context.pages if page is not self.anchor
                and not page.url.startswith(("edge://", "chrome://")) and not page.is_closed()]

    def _ensure(self):
        from playwright.sync_api import sync_playwright
        if self.context and self.browser and self.browser.is_connected():
            return self.context
        self._close()
        self.playwright = sync_playwright().start()
        self.headless = settings.VNU_BROWSER_HEADLESS and not settings.VNU_ALLOW_MANUAL_VERIFICATION
        options = {"headless": self.headless, "accept_downloads": True}
        if not self.headless:
            options["args"] = ["--disable-blink-features=AutomationControlled"]
            options["ignore_default_args"] = ["--enable-automation"]
        channel = settings.VNU_BROWSER_CHANNEL
        if channel not in ("chrome", "msedge", "chromium"):
            self.playwright.stop()
            self.playwright = None
            raise ValueError("VNU_BROWSER_CHANNEL must be one of: msedge, chrome, chromium")
        if channel and channel != "chromium":
            options["channel"] = channel
        profile_root = settings.VNU_BROWSER_PROFILE_DIR
        profile = profile_root / (channel or "chromium")
        profile.mkdir(parents=True, exist_ok=True)
        try:
            self.browser_creation_count += 1
            print(f"[BROWSER CREATE] #{self.browser_creation_count}")
            self.context = self.playwright.chromium.launch_persistent_context(str(profile), **options)
            self.context_id = f"CONTEXT-{self.browser_creation_count:03d}"
            print(f"[BROWSER]\nPersistent context created\ncontext_id={self.context_id}")
        except Exception as error:
            message = str(error).lower()
            missing_browser = "executable doesn't exist" in message or ("distribution" in message and "not found" in message)
            if missing_browser:
                browser_name = {"msedge": "Microsoft Edge", "chrome": "Google Chrome", "chromium": "Playwright Chromium"}[channel]
                raise RuntimeError(
                    f"Configured browser {browser_name} is unavailable. Install it or set "
                    "VNU_BROWSER_CHANNEL to msedge, chrome, or chromium. No browser fallback was attempted."
                ) from error
            raise
        self.channel = channel
        self.browser = self.context.browser
        self.anchor = self.context.pages[0] if self.context.pages else self.context.new_page()
        try:
            setattr(self.anchor, "_runtime_page_id", "PAGE-ANCHOR")
        except Exception:
            pass

        # Import earlier snapshot once into the new profile.
        state = settings.VNU_BROWSER_STATE_FILE
        if state.exists():
            try:
                snapshot = json.loads(state.read_text(encoding="utf-8"))
                current = {(item["name"], item["domain"], item["path"]) for item in self.context.cookies()}
                def institution_cookie(item):
                    domain = item.get("domain", "").lstrip(".").lower()
                    challenge_cookie = item.get("name", "").lower().startswith(("cf_", "cf-", "__cf"))
                    return not challenge_cookie and any(domain == trusted or domain.endswith("." + trusted) for trusted in TRUSTED_LOGIN_DOMAINS)
                missing = [item for item in snapshot.get("cookies", [])
                           if institution_cookie(item)
                           and (item["name"], item["domain"], item["path"]) not in current
                           and (item.get("expires", -1) == -1 or item["expires"] > time.time())]
                if missing:
                    self.context.add_cookies(missing)
            except Exception:
                pass
        return self.context

    def save_state(self):
        if not self.context:
            return
        state = settings.VNU_BROWSER_STATE_FILE
        partial = state.with_suffix(".json.part")
        try:
            partial.write_text(json.dumps(self.context.storage_state()), encoding="utf-8")
            os.replace(partial, state)
        finally:
            partial.unlink(missing_ok=True)

    @contextmanager
    def session(self):
        self._ensure()
        context = self.context
        try:
            yield self.browser, context
        finally:
            try:
                self.save_state()
            except Exception:
                pass
            # Only close transient tabs that were not retained or pinned.
            for page in self.article_pages():
                if page in self.retained_pages:
                    continue
                if page is self.current_article_page:
                    self.current_article_page = None
                if page is self.auth_page:
                    self.auth_page = None
                try:
                    page.close(run_before_unload=True)
                except Exception:
                    pass

            deadline = time.monotonic() + 2
            while any(page not in self.retained_pages for page in self.article_pages()) and time.monotonic() < deadline:
                try:
                    self.anchor.wait_for_timeout(100)
                except Exception:
                    break

    def recover_after_close(self):
        # Do not restart browser if in a challenge session or if browser is still alive.
        if self.challenge_session:
            return False
        if self.browser and self.browser.is_connected():
            return False
        self._close()
        return True

    def _close(self):
        try:
            if self.context:
                try:
                    self.save_state()
                except Exception:
                    pass
                try:
                    self.context.close()
                except Exception:
                    pass
            if self.browser:
                try:
                    self.browser.close()
                except Exception:
                    pass
        finally:
            if self.playwright:
                try:
                    self.playwright.stop()
                except Exception:
                    pass
            self.context = self.browser = self.playwright = None
            self.authenticated = False
            self.anchor = None
            self.current_article_page = None
            self.auth_page = None
            self.retained_pages.clear()
            self.pending_verifications.clear()
            self.pending_providers.clear()
            self.verified_pages.clear()
            self.challenge_session = False
            self.focus_requested.clear()

    def request_shutdown(self):
        self.stopping.set()

    async def shutdown(self):
        print("[BROWSER]\nShutting down BrowserManager")
        self.request_shutdown()
        await self.run(self._close)
        self.executor.shutdown(wait=False)


# Singleton instance
BrowserWorker = BrowserManager
browser_manager = BrowserManager()
browser_worker = browser_manager
