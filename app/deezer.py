"""Client Deezer (API publique, sans clé) : retrouver un OP/ED en version complète
(ISRC → Spotify précis) et trouver les albums de bande originale (OST) d'un anime."""
import re
import time

from . import match
from .net import ApiError, Throttle, get_json

API = "https://api.deezer.com"
throttle = Throttle(0.15)  # quota Deezer : 50 requêtes / 5 s


def _get(path: str, **params):
    for attempt in range(4):
        data = get_json(API + path, throttle, params=params)
        err = data.get("error") if isinstance(data, dict) else None
        if not err:
            return data
        if err.get("code") == 4:  # quota dépassé : on souffle
            time.sleep(5 * (attempt + 1))
            continue
        if err.get("code") == 800:  # objet inexistant
            return None
        raise ApiError(f"Deezer {path}: {err}")
    raise ApiError(f"Deezer {path}: quota")


def track(track_id: int) -> dict | None:
    return _get(f"/track/{track_id}")


def best_track(title: str, artist: str) -> dict | None:
    """Meilleur titre Deezer pour un OP/ED (version originale, pas une reprise)."""
    seen, scored = set(), []
    first_artist = (match.split_artists(artist) or [artist])[0]

    def consider(candidates):
        for c in candidates:
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            ts = match.title_sim(title, c.get("title") or "")
            ts = max(ts, match.title_sim(title, c.get("title_short") or ""))
            ar = match.artist_sim(artist, (c.get("artist") or {}).get("name") or "")
            if ts < 0.8 or ar < 0.5:
                continue
            score = ts * 0.6 + ar * 0.4 - match.penalty(c.get("title") or "", title)
            scored.append((score, c))

    for q in (f"{first_artist} {title}", title):
        consider((_get("/search", q=q, limit=15) or {}).get("data") or [])
        if scored:
            break
    if not scored and artist:
        # Titre rangé en japonais (残酷な夜に輝け) : la recherche Deezer ne comprend pas le
        # romaji → on parcourt le top 100 de l'artiste, comparé après romanisation.
        for name in match.split_artists(artist)[:2]:
            found = (_get("/search/artist", q=name, limit=5) or {}).get("data") or []
            # homonymes (« LiSA » / « Lisa ») : le plus suivi d'abord
            found = sorted((a for a in found if match.artist_sim(name, a.get("name") or "") >= 0.8),
                           key=lambda a: -(a.get("nb_fan") or 0))
            for ar in found[:2]:
                consider((_get(f"/artist/{ar['id']}/top", limit=100) or {}).get("data") or [])
                if scored:
                    break
            if scored:
                break
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return scored[0][1] if scored[0][0] >= 0.7 else None


# --------------------------------------------------------------------------- OST
_OST_WORDS = re.compile(
    r"soundtrack|\bost\b|original score|サウンドトラック|サントラ|music collection|\bbgm\b|"
    r"original sound|オリジナル", re.IGNORECASE)
_NOT_OST = re.compile(
    r"piano|music box|orgel|cover|lofi|lo-fi|relax|8.?bit|guitar|violin|epic|inspired|tribute|"
    r"kalimba|lullaby|chill|karaoke|game|anime hits|best of anime|anime songs|remix|nightcore|"
    r"trap|orchestral version|vol\. ?\d+ ?- ?covers|hits|english|instrumental|改|op ?x ?ed|"
    r"music lah|selected songs from|netflix|live.?action|fan.?made|soundtrack collection", re.IGNORECASE)
# Labels de distribution indé / films occidentaux : reprises de fans, live-action, séries Netflix
_BAD_LABEL = re.compile(
    r"\brecords dk\b|distrokid|tunecore|cd ?baby|routenote|ditto|soundon|netflix|masterworks|"
    r"lakeshore|var[eè]se|sony classical", re.IGNORECASE)
_BAD_ARTIST = re.compile(r"piano|cover|anime kei|kaiser|orchestra(l)? (of|des)|lofi", re.IGNORECASE)
# Éditeurs japonais d'anime : signe d'une vraie OST
_JP_LABEL = re.compile(
    r"aniplex|lantis|pony ?canyon|ポニーキャニオン|kadokawa|\bvap\b|avex|toho|king records|flying ?dog|"
    r"victor|sony music|nbcuniversal|sunrise|bandai|frontier works|columbia|warner bros|toy'?s factory|"
    r"\bdmm\b|universal music llc|key sounds|mappa|starchild|geneon|tms|lantis|movic|f\.i\.x|"
    r"[぀-ヿ一-鿿]", re.IGNORECASE)
_ANIME_HINT = re.compile(r"\btv\b|anime|animation|series|テレビ|アニメ", re.IGNORECASE)


def find_ost_albums(names: list[str], year: int, limit: int = 12) -> list[dict]:
    """Albums de bande originale d'une série : le titre doit contenir le nom de l'anime
    ET un mot « soundtrack/OST » ; ni reprises (piano, lofi…), ni fan-made, ni live-action."""
    keys = sorted({match.norm(n) for n in names if len(match.norm(n)) >= 3}, key=len, reverse=True)
    if not keys:
        return []
    found: dict[int, dict] = {}
    queries = []
    for n in names[:4]:
        if n:
            queries += [f"{n} original soundtrack", f"{n} OST"]
    for q in dict.fromkeys(queries):
        data = _get("/search/album", q=q, limit=25) or {}
        for a in data.get("data") or []:
            t = a.get("title") or ""
            if a["id"] in found or not _OST_WORDS.search(t) or _NOT_OST.search(t):
                continue
            nt = f" {match.norm(t)} "
            hit = next((k for k in keys if f" {k} " in nt), None)
            if not hit:
                continue
            # nom court/commun (« Orange », « Monster ») : exiger un vrai marqueur d'anime
            if len(hit) < 9 and not re.search(r"anime|tv|\"|「|『|“", t, re.IGNORECASE):
                continue
            found[a["id"]] = a
        if len(found) >= limit * 3:
            break
    albums = []
    for a in found.values():
        full = _get(f"/album/{a['id']}")
        if not full:
            continue
        rel = full.get("release_date") or ""
        if year and rel[:4].isdigit() and int(rel[:4]) < year - 1:
            continue  # sorti bien avant l'anime : homonyme
        label = full.get("label") or ""
        artist = (full.get("artist") or {}).get("name") or ""
        if _BAD_LABEL.search(label) or _BAD_ARTIST.search(artist):
            continue
        if not label or match.norm(label) == match.norm(artist):
            continue  # auto-édité : quasi toujours une reprise
        albums.append(full)
    # Singles et EP de fans : une vraie OST a au moins 10 pistes, ou 5+ si son
    # compositeur signe une autre OST de la série.
    artist_of = lambda a: match.norm((a.get("artist") or {}).get("name") or "")
    composers = {artist_of(a) for a in albums if (a.get("nb_tracks") or 0) >= 10}
    albums = [a for a in albums if (a.get("nb_tracks") or 0) >= 10 or (
        (a.get("nb_tracks") or 0) >= 5 and artist_of(a) in composers)]
    # même album en plusieurs éditions : on garde la plus complète
    best: dict[str, dict] = {}
    for a in albums:
        k = match.norm(re.sub(r"\(.*?\)|\[.*?\]", "", a.get("title") or ""))
        if k not in best or (a.get("nb_tracks") or 0) > (best[k].get("nb_tracks") or 0):
            best[k] = a
    recurring = [artist_of(a) for a in best.values()]

    def score(a):
        return (2 * bool(_ANIME_HINT.search(a.get("title") or ""))
                + bool(_JP_LABEL.search(a.get("label") or ""))
                + 0.5 * (recurring.count(artist_of(a)) > 1),
                a.get("nb_tracks") or 0)

    kept = sorted(best.values(), key=score, reverse=True)[:limit]
    return sorted(kept, key=lambda a: a.get("release_date") or "")


def album_tracks(album_id: int) -> list[dict]:
    out, index = [], 0
    while True:
        data = _get(f"/album/{album_id}/tracks", limit=100, index=index) or {}
        out += data.get("data") or []
        if not data.get("next"):
            return out
        index += 100
