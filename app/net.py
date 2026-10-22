"""Client HTTP partagé : délai mini entre requêtes par API + reprise sur 429/5xx."""
import threading
import time

import httpx

from . import config

client = httpx.Client(timeout=30, headers={"User-Agent": config.USER_AGENT}, follow_redirects=True)


class Throttle:
    """Espace les appels vers une même API (sûr entre threads)."""

    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


class ApiError(Exception):
    pass


def request(method: str, url: str, throttle: Throttle | None = None, retries: int = 4, **kw) -> httpx.Response:
    for attempt in range(retries + 1):
        if throttle:
            throttle.wait()
        try:
            r = client.request(method, url, **kw)
        except httpx.HTTPError as e:
            if attempt == retries:
                raise ApiError(f"{url}: {e}") from e
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == retries:
                raise ApiError(f"{url}: HTTP {r.status_code}")
            wait = r.headers.get("retry-after")
            time.sleep(min(float(wait), 120) if wait and wait.replace(".", "").isdigit() else 3 * (attempt + 1))
            continue
        return r
    raise ApiError(url)


def get_json(url: str, throttle: Throttle | None = None, **kw):
    r = request("GET", url, throttle, **kw)
    if r.status_code >= 400:
        raise ApiError(f"{url}: HTTP {r.status_code} {r.text[:200]}")
    return r.json()
