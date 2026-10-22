"""Client AnimeThemes (https://api.animethemes.moe) : OP / ED / inserts de chaque anime.

Gratuit, sans clé, limité à 90 requêtes/min → on reste vers 70/min.
Chaque thème donne titre, artistes, épisodes, la vidéo .webm et l'audio .ogg (version TV),
et parfois des liens Spotify / YouTube saisis par la communauté.
"""
import re

from .net import Throttle, get_json

API = "https://api.animethemes.moe"
throttle = Throttle(0.85)

FULL_INCLUDE = ",".join([
    "animethemes.group",
    "animethemes.song.artists",
    "animethemes.song.resources",
    "animethemes.animethemeentries.videos.audio",
    "series",
    "resources",
    "animesynonyms",
])
LIGHT_FIELDS = "id,name,slug,year,season,media_format"


def _paged(url: str, params: dict) -> list[dict]:
    out = []
    data = get_json(url, throttle, params=params)
    while True:
        out.extend(data.get("anime") or [])
        nxt = (data.get("links") or {}).get("next")
        if not nxt:
            return out
        data = get_json(nxt, throttle)


def by_kitsu(kitsu_ids: list[int]) -> list[dict]:
    """Fiches légères (id, nom, séries) des anime AnimeThemes liés à ces ids Kitsu.
    (Le filtre par site restreint aussi les ressources incluses : les liens MAL & co
    viennent ensuite de by_ids.)"""
    out = []
    for i in range(0, len(kitsu_ids), 25):
        out += _paged(f"{API}/anime", {
            "filter[has]": "resources", "filter[site]": "Kitsu",
            "filter[external_id]": ",".join(map(str, kitsu_ids[i:i + 25])),
            "include": "series", "fields[anime]": LIGHT_FIELDS, "page[size]": 50,
        })
    return out


def by_ids(at_ids: list[int]) -> list[dict]:
    out = []
    for i in range(0, len(at_ids), 25):
        out += _paged(f"{API}/anime", {
            "filter[anime][id]": ",".join(map(str, at_ids[i:i + 25])),  # filtre limité à anime (sinon appliqué aux inclusions)
            "include": FULL_INCLUDE, "page[size]": 50,
        })
    return out


def series_anime(slug: str) -> list[dict]:
    """Toutes les fiches (légères + synonymes) d'une série AnimeThemes."""
    data = get_json(f"{API}/series/{slug}", throttle, params={
        "include": "anime.animesynonyms", f"fields[anime]": LIGHT_FIELDS})
    return (data.get("series") or {}).get("anime") or []


def search(q: str) -> list[dict]:
    data = get_json(f"{API}/anime", throttle, params={
        "q": q, "include": "animesynonyms,series", "fields[anime]": LIGHT_FIELDS, "page[size]": 15})
    return data.get("anime") or []


def external_id(anime: dict, site: str) -> int | None:
    for r in anime.get("resources") or []:
        if r.get("site") == site and str(r.get("external_id") or "").isdigit():
            return int(r["external_id"])
    return None


def names(anime: dict) -> list[str]:
    out = [anime.get("name") or ""]
    out += [s.get("text") or "" for s in anime.get("animesynonyms") or []]
    return [n for n in out if n]


_SPOTIFY_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?track/([A-Za-z0-9]{22})")
_YT_RE = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/|music\.youtube\.com/watch\?v=)([A-Za-z0-9_-]{11})")


def themes(anime: dict) -> list[dict]:
    """Thèmes d'une fiche complète → titres prêts à stocker."""
    out = []
    for t in anime.get("animethemes") or []:
        if t.get("group"):  # versions doublées / alternatives : on garde l'original
            continue
        kind = t.get("type") or "IN"
        if kind not in ("OP", "ED", "IN"):
            kind = "IN"
        song = t.get("song") or {}
        artists = []
        for a in song.get("artists") or []:
            piv = a.get("artistsong") or {}
            artists.append(piv.get("alias") or a.get("name") or "")
        entries = t.get("animethemeentries") or []
        entry = next((e for e in entries if e.get("videos")), entries[0] if entries else {})
        videos = entry.get("videos") or []
        video = next((v for v in videos if v.get("audio")), videos[0] if videos else {})
        spotify = youtube = ""
        for res in song.get("resources") or []:
            link = res.get("link") or ""
            m = _SPOTIFY_RE.search(link)
            if m and not spotify:
                spotify = m.group(1)
            m = _YT_RE.search(link)
            if m and not youtube:
                youtube = m.group(1)
        out.append({
            "key": f"at:{t['id']}",
            "kind": kind,
            "song_id": song.get("id"),
            "seq": t.get("sequence") or 0,
            "slug": t.get("slug") or kind,
            "title": song.get("title") or "Titre inconnu",
            "artist": ", ".join(a for a in artists if a),
            "episodes": entry.get("episodes") or "",
            "at_video": video.get("link") or "",
            "at_audio": (video.get("audio") or {}).get("link") or "",
            "plays": max((e.get("tracks_count") or 0 for e in entries), default=0),
            "spoiler": any(e.get("spoiler") for e in entries),
            "nsfw": any(e.get("nsfw") for e in entries),
            "spotify": spotify,
            "youtube": youtube,
        })
    return out
