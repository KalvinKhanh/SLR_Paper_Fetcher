"""Observe authorized login/full-text state without interacting with challenges."""

import re
from urllib.parse import urlparse

TRUSTED_LOGIN_DOMAINS = ("openathens.net", "vnu.edu.vn", "vnuhcm.edu.vn")


def trusted_login_page(page):
    host = (urlparse(page.url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in TRUSTED_LOGIN_DOMAINS)


def visible(page, selector):
    try:
        locator = page.locator(selector)
        return any(locator.nth(i).is_visible() for i in range(min(locator.count(), 8)))
    except Exception:
        return False


def is_authenticated(page):
    """Require an actual signed-in/institution marker, not only a redirected URL."""
    if visible(page, 'input[type="password"]'):
        return False
    if trusted_login_page(page):
        return False
    try:
        # Markers indicating authentication or institutional access
        markers = page.get_by_text(re.compile(
            r"sign out|log out|access provided by|brought to you by|subscribed by|institutional access",
            re.I))
        if any(markers.nth(i).is_visible() for i in range(min(markers.count(), 10))):
            return True

        # Check for institution name in header, navigation, banner, or institution badges
        inst_containers = page.locator(
            'header, nav, [role="banner"], [role="navigation"], '
            '.header, .navbar, .top-bar, .metanav, .xplore-meta-nav, '
            '[class*="institution" i], [id*="institution" i], '
            '[class*="branding" i], [id*="branding" i], '
            '[class*="identity" i], [id*="identity" i], '
            '[class*="access" i], [id*="access" i]'
        )
        institution = inst_containers.get_by_text(re.compile(
            r"Vietnam National University|Viet Nam National Uni|VNU-HCM|VNUHCM|ĐHQG-HCM", re.I))
        return any(institution.nth(i).is_visible() for i in range(min(institution.count(), 8)))
    except Exception:
        return False


def has_fulltext_access(page):
    """Detect if the page already grants full-text access or has a direct, non-paywalled PDF download button."""
    if trusted_login_page(page):
        return False
    try:
        # 1. Text markers of full-text / open / institutional access
        marker = page.get_by_text(re.compile(
            r"full[- ]text access|full access|open access|you have (?:full )?access|access provided by|subscribed content",
            re.I))
        if any(marker.nth(i).is_visible() for i in range(min(marker.count(), 10))):
            return True

        # 2. Check if a non-purchase PDF download button is already visible and ready to use
        # If the page already has "Download PDF", "Download chapter PDF", "View PDF", etc.
        # without requiring a purchase/paywall, full-text access is already granted!
        pdf_buttons = page.locator(
            'a:has-text("Download PDF"), button:has-text("Download PDF"), '
            'a:has-text("Download chapter PDF"), button:has-text("Download chapter PDF"), '
            'a:has-text("Download book PDF"), button:has-text("Download book PDF"), '
            'a:has-text("View PDF"), button:has-text("View PDF"), '
            'a.pdf-btn, a[href*="/stamp/"], a[href*="/content/pdf/"], a[data-article-pdf]'
        )
        for i in range(min(pdf_buttons.count(), 6)):
            btn = pdf_buttons.nth(i)
            if not btn.is_visible():
                continue
            text = (btn.inner_text() or "").lower()
            aria = (btn.get_attribute("aria-label") or "").lower()
            href = (btn.get_attribute("href") or "").lower()
            combined = f"{text} {aria} {href}"
            if not any(w in combined for w in ("purchase", "buy", "order", "rent", "cart", "checkout", "subscribe", "pricing", "$", "eur", "usd")):
                return True

        return False
    except Exception:
        return False


def requires_mfa(page):
    if not trusted_login_page(page):
        return False
    if visible(page, 'input[autocomplete="one-time-code"], input[name*="otp" i], input[name*="verification" i]'):
        return True
    try:
        marker = page.get_by_text(re.compile(r"enter (?:your |the )?(?:verification|security) code|approve (?:the |this )?sign.?in|two.factor authentication|multi.factor authentication", re.I))
        return any(marker.nth(i).is_visible() for i in range(min(marker.count(), 5)))
    except Exception:
        return False
