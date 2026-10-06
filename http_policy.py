"""Share modest request spacing and server cooldowns across public providers."""

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
from urllib.parse import urlparse
import requests

_guard = Lock()
_starts = {}
_cooldowns = {}


def polite_get(url, *, session=None, **kwargs):
    host = (urlparse(url).hostname or "").lower()
    interval = 1.0 if host in ("api.semanticscholar.org", "doi.org", "export.arxiv.org") else 0.5
    with _guard:
        now = time.monotonic()
        if _cooldowns.get(host, 0) > now:
            raise requests.HTTPError("Provider is in Retry-After cooldown")
        start = max(now, _starts.get(host, 0) + interval)
        _starts[host] = start
    if start > now:
        time.sleep(start - now)
    with _guard:
        if _cooldowns.get(host, 0) > time.monotonic():
            raise requests.HTTPError("Provider is in Retry-After cooldown")
    response = (session.get if session is not None else requests.get)(url, **kwargs)
    if response.status_code == 429:
        retry = response.headers.get("Retry-After", "60")
        try:
            seconds = float(retry)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError):
                seconds = 60
        with _guard:
            _cooldowns[host] = time.monotonic() + max(1, seconds)
    return response
