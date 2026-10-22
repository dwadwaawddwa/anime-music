"""Recherche YouTube sans clé API (yt-dlp, résultats « à plat » : rien n'est téléchargé)."""
import logging
import threading

from . import match
from .net import Throttle

log = logging.getLogger("music.youtube")
throttle = Throttle(1.5)
_lock = threading.Lock()

try:
    from yt_dlp import YoutubeDL
except Exception:  # pragma: no cover
    YoutubeDL = None

_OPTS = {"quiet": True, "no_warnings": True, "skip_download": True,
         "extract_flat": "in_playlist", "noplaylist": True}


def search(q: str, n: int = 8) -> list[dict]:
    if YoutubeDL is None:
        return []
    throttle.wait()
    with _lock, YoutubeDL(_OPTS) as ydl:
        info = ydl.extract_info(f"ytsearch{n}:{q}", download=False) or {}
    return [e for e in info.get("entries") or [] if e and e.get("id")]


def _query(title: str, artist: str, context: str) -> str:
    first_artist = (match.split_artists(artist) or [artist])[0]
    return " ".join(x for x in (first_artist, title, context) if x)


def _safe_search(q: str) -> list[dict]:
    try:
        return search(q)
    except Exception as e:  # YouTube change souvent : on ne casse pas le worker
        log.warning("yt search %r: %s", q, e)
        return []


def best_video(title: str, artist: str, context: str = "") -> tuple[str, int]:
    """(id, popularité) : la meilleure vidéo — chaîne « Artiste - Topic » (audio officiel)
    ou clip officiel, jamais une reprise / nightcore / AMV si on peut l'éviter — et le plus
    grand nombre de vues parmi les vidéos de ce titre (le clip officiel en a souvent
    bien plus que l'audio « Topic » retenu pour l'écoute)."""
    first_artist = (match.split_artists(artist) or [artist])[0]
    results = _safe_search(_query(title, artist, context))
    best, best_score = "", 0.0
    views = _popularity(results, title, artist)
    for e in results:
        vt = e.get("title") or ""
        ch = e.get("channel") or e.get("uploader") or ""
        dur = e.get("duration") or 0
        if dur and (dur > 15 * 60 or dur < 45):
            continue
        t = match.contains_tokens(title, vt)
        a = max(match.artist_sim(artist, ch.replace(" - Topic", "")),
                1.0 if first_artist and match.contains_tokens(first_artist, vt) >= 0.99 else 0.0)
        if t < 0.75:
            continue
        score = t * 0.6 + a * 0.4 - match.penalty(vt, title)
        if ch.endswith(" - Topic") and a >= 0.6:
            score += 0.25
        if "official" in vt.lower() or "music video" in vt.lower():
            score += 0.05
        if score > best_score:
            best, best_score = e["id"], score
    return (best, views) if best_score >= 0.65 else ("", -1)


def _popularity(results: list[dict], title: str, artist: str) -> int:
    """Vues max des vidéos qui sont bien ce titre de cet artiste (hors reprises)."""
    first_artist = (match.split_artists(artist) or [artist])[0]
    best = -1
    for e in results:
        vt, ch = e.get("title") or "", (e.get("channel") or e.get("uploader") or "").replace(" - Topic", "")
        a = max(match.artist_sim(artist, ch),
                1.0 if first_artist and match.contains_tokens(first_artist, vt) >= 0.99 else 0.0)
        if match.contains_tokens(title, vt) >= 0.75 and a >= 0.6 and match.penalty(vt, title) < 0.2:
            best = max(best, e.get("view_count") or -1)
    return best


def video_views(title: str, artist: str, context: str = "") -> int:
    """Popularité YouTube d'un titre dont la vidéo est déjà connue."""
    return _popularity(_safe_search(_query(title, artist, context)), title, artist)
