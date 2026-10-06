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
    try:
        markers = page.get_by_text(re.compile(
            r"sign out|log out|access provided by",
            re.I))
        if trusted_login_page(page):
            return False
        if any(markers.nth(i).is_visible() for i in range(min(markers.count(), 10))):
            return True
        institution = page.locator('header, [role="banner"]').get_by_text(re.compile(
            r"Vietnam National University|Viet Nam National Uni", re.I))
        return any(institution.nth(i).is_visible() for i in range(min(institution.count(), 8)))
    except Exception:
        return False


def has_fulltext_access(page):
    try:
        marker = page.get_by_text(re.compile(r"^Full text access$|^Open access$|you have (?:full )?access to (?:this |the )?article", re.I))
        return any(marker.nth(i).is_visible() for i in range(min(marker.count(), 10)))
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
