"""Read-only challenge detection and manual verification waits."""

import time
from urllib.parse import urlparse


def is_sciencedirect_article_page(page):
    """Accurately detect whether the page is a ScienceDirect / Elsevier article.

    Checks:
    - ScienceDirect / publisher article DOM markers
    - Article title
    - Download / View PDF button
    - Abstract section / body
    - Journal/article metadata
    - Confirms absence of Cloudflare challenge / robot verification markers.
    """
    if page.is_closed():
        return False
    try:
        return page.evaluate("""() => {
            const title = (document.title || '').toLowerCase().trim();
            const text = (document.body?.innerText || '').slice(0, 15000).toLowerCase();
            const url = window.location.href.toLowerCase();

            // Challenge markers must be absent
            const hasChallenge = (
                title.includes('just a moment') ||
                title.includes('cloudflare') ||
                title.includes('attention required') ||
                text.includes('are you a robot') ||
                text.includes('verify you are human') ||
                text.includes('verify you are a human') ||
                text.includes('verify that you are human') ||
                text.includes('please confirm you are a human') ||
                text.includes('checking your browser') ||
                text.includes('performing security verification') ||
                Boolean(document.querySelector('iframe[src*="challenges.cloudflare.com"], #challenge-stage, #cf-challenge-running'))
            );
            if (hasChallenge) return false;

            const isSd = url.includes('sciencedirect.com') || url.includes('elsevier.com') || title.includes('sciencedirect') || text.includes('sciencedirect');

            // DOM indicators for ScienceDirect article
            const hasSdDom = Boolean(
                document.querySelector(
                    '#article-header, #abstracts, #body, .article-doc, .Body, ' +
                    '[data-article-id], meta[name="citation_title"], meta[name="citation_pdf_url"], ' +
                    '#mathjax-container, .pdf-download, .Article, #author-group, .navigation-bar'
                )
            );

            // PDF or Full Text buttons
            const hasPdfBtn = Boolean(
                document.querySelector(
                    'a[aria-label*="View PDF" i], button[aria-label*="View PDF" i], ' +
                    'a[aria-label*="Download PDF" i], button[aria-label*="Download PDF" i], ' +
                    'a:has-text("View PDF"), button:has-text("View PDF")'
                ) ||
                [...document.querySelectorAll('a, button')].some(el => {
                    const t = (el.innerText || '').trim().toLowerCase();
                    return t === 'view pdf' || t === 'download pdf' || t === 'pdf';
                })
            );

            // Abstract section
            const hasAbstract = Boolean(
                document.querySelector('h2#abstracts, .abstract, .Abstract, #preview-section, #front') ||
                text.includes('abstract') || text.includes('keywords') || text.includes('references')
            );

            // If on ScienceDirect, having SD DOM or (PDF button + article text) confirms it
            if (isSd && (hasSdDom || (hasPdfBtn && text.length > 200) || (hasAbstract && text.length > 300))) {
                return true;
            }

            // General publisher article detection (Springer, Wiley, IEEE, ACM, etc.)
            const hasGeneralArticle = Boolean(
                document.querySelector('meta[name="citation_title"], [data-article-id], .c-article-header') ||
                (text.length > 500 && (text.includes('abstract') || text.includes('references') || text.includes('doi.org/')))
            );

            return Boolean(hasGeneralArticle && !hasChallenge);
        }""")
    except Exception:
        return False


def detect_cloudflare_challenge(page):
    """Check if the page currently shows a Cloudflare or anti-bot challenge."""
    if page.is_closed():
        return False
    try:
        return page.evaluate("""() => {
            const title = (document.title || '').toLowerCase().trim();
            const text = (document.body?.innerText || '').slice(0, 12000).toLowerCase();

            if (title.includes('just a moment') || title.includes('cloudflare') || title.includes('attention required! | cloudflare')) {
                return true;
            }
            if (
                text.includes('are you a robot?') ||
                text.includes('please confirm you are a human') ||
                text.includes('verify you are human') ||
                text.includes('verify you are a human') ||
                text.includes('verify that you are human') ||
                text.includes('checking your browser') ||
                text.includes('checking if the site connection is secure') ||
                text.includes('performing security verification') ||
                text.includes('/cdn-cgi/challenge')
            ) {
                return true;
            }
            const widget = [...document.querySelectorAll('iframe[src*="challenges.cloudflare.com"], #challenge-stage, #cf-challenge-running')]
                .some(el => {
                    const box = el.getBoundingClientRect(), style = getComputedStyle(el);
                    return box.width > 0 && box.height > 0 && style.visibility !== 'hidden'
                        && style.display !== 'none' && style.opacity !== '0';
                });
            return Boolean(widget);
        }""")
    except Exception:
        return False


def verification_state(page):
    """Return challenge, ready, loading or closed; navigation is not success."""
    if page.is_closed():
        return "closed"
    if detect_cloudflare_challenge(page):
        return "challenge"
    if is_sciencedirect_article_page(page):
        return "ready"
    try:
        state = page.evaluate("""() => ({
            title: document.title.toLowerCase().trim(),
            text: (document.body?.innerText || '').slice(0, 12000).toLowerCase(),
            ready: document.readyState,
            pdf: document.contentType === 'application/pdf',
        })""")
        if any(marker in state["title"] or marker in state["text"] for marker in (
                "redirecting", "please wait", "verification in progress", "validating your browser")):
            return "loading"
        if state["pdf"]:
            return "ready"
        if state["ready"] in ("complete", "interactive") and len(state["text"].strip()) >= 40:
            return "ready"
    except Exception:
        if page.is_closed():
            return "closed"
    return "loading"


def wait_for_browser_verification(page, timeout_seconds=None, status_callback=None,
                                  stop_event=None, on_challenge=None, stable_seconds=1.5,
                                  previously_challenged=False, focus_event=None):
    """Observe state only. Never reloads, navigates or clicks during verification."""
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    waiting = previously_challenged
    if waiting and status_callback:
        status_callback("waiting_for_verification")
    ready_since = None
    last_url = None
    challenge_seen = previously_challenged

    # Runtime page id for logging
    page_id = getattr(page, "_runtime_page_id", f"PAGE-{id(page):x}")

    while True:
        if stop_event is not None and stop_event.is_set():
            return False
        if focus_event is not None and focus_event.is_set():
            focus_event.clear()
            if not page.is_closed():
                try:
                    page.bring_to_front()
                except Exception:
                    pass
        state = verification_state(page)
        if state == "closed":
            return False
        if state == "challenge":
            ready_since = None
            if not waiting:
                waiting = True
                challenge_seen = True
                print(f"[CF]\nChallenge detected\npage_id={page_id}")
                print(f"[CF]\nUsing SAME article page for verification")
                print(f"[AUTH]\nWAITING_FOR_HUMAN")
                print(f"[AUTH]\nVerification monitor started")
                if on_challenge:
                    on_challenge(page)
                if status_callback:
                    status_callback("waiting_for_verification")
        elif state == "ready":
            if not waiting:
                return True
            if challenge_seen:
                print(f"[AUTH]\nChallenge disappeared")
                challenge_seen = False

            # If ScienceDirect article DOM appeared, verification is definitively complete!
            if is_sciencedirect_article_page(page):
                print(f"[AUTH]\nArticle page detected")
                print(f"[AUTH]\nVERIFICATION_COMPLETED")
                if status_callback:
                    status_callback("verification_complete")
                return True

            now = time.monotonic()
            if ready_since is None or page.url != last_url:
                ready_since = now
            if now - ready_since >= stable_seconds:
                print(f"[AUTH]\nArticle page detected")
                print(f"[AUTH]\nVERIFICATION_COMPLETED")
                if status_callback:
                    status_callback("verification_complete")
                return True
        else:
            ready_since = None

        last_url = page.url
        if deadline is not None and time.monotonic() >= deadline:
            return False
        try:
            # Observation loop only: no clicks, no reloads, no goto
            page.wait_for_timeout(200)
        except Exception:
            if page.is_closed():
                return False
