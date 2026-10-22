"""AnisongDB (https://anisongdb.com, base d'Anime Music Quiz) : complète AnimeThemes.

- donne l'artiste quand AnimeThemes ne l'a pas (vieux One Piece…) ;
- liste les inserts et les OP/ED absents d'AnimeThemes, et les animes qu'il n'a pas du tout.
Requêtes par ids MyAnimeList (500 max) ; on obtient ces ids via AnimeThemes ou Kitsu.
"""
import re

from .net import ApiError, Throttle, get_json, request

API = "https://anisongdb.com/api"
throttle = Throttle(1.0)

_TYPE = re.compile(r"^(Opening|Ending|Insert)\s*(?:Song)?\s*(\d+)?", re.IGNORECASE)
KINDS = {"opening": "OP", "ending": "ED", "insert": "IN"}


def by_mal(mal_ids: list[int]) -> list[dict]:
    out = []
    ids = sorted({int(i) for i in mal_ids if i})
    for i in range(0, len(ids), 400):
        r = request("POST", f"{API}/mal_ids_request", throttle, json={"mal_ids": ids[i:i + 400]})
        if r.status_code >= 400:
            raise ApiError(f"AnisongDB {r.status_code}: {r.text[:200]}")
        out += r.json() or []
    songs = {}
    for s in (parse(x) for x in out):
        if s and s["ann_song_id"] not in songs:
            songs[s["ann_song_id"]] = s
    return list(songs.values())


def parse(x: dict) -> dict | None:
    if x.get("isDub") or x.get("isRebroadcast"):
        return None
    m = _TYPE.match(x.get("songType") or "")
    if not m:
        return None
    kind = KINDS[m.group(1).lower()]
    seq = int(m.group(2)) if m.group(2) else 0
    links = x.get("linked_ids") or {}
    return {
        "ann_song_id": x.get("annSongId"),
        "song_id": x.get("amqSongId"),
        "kind": kind,
        "seq": seq,
        "title": x.get("songName") or "",
        "artist": x.get("songArtist") or "",
        "mal": links.get("myanimelist"),
        "kitsu": links.get("kitsu"),
        "anime": x.get("animeJPName") or x.get("animeENName") or "",
        "vintage": x.get("animeVintage") or "",
        "anime_type": x.get("animeType") or "",
        "length": int(x.get("songLength") or 0),
        # « difficulté » AMQ = % de joueurs qui ont reconnu l'anime (plus c'est haut, plus c'est facile)
        "rate": x.get("songDifficulty"),
    }


def kitsu_to_mal(kitsu_ids: list[int]) -> dict[int, int]:
    """Ids MyAnimeList des fiches Kitsu (mappings Kitsu, 20 par requête)."""
    out: dict[int, int] = {}
    ids = [i for i in kitsu_ids if 0 < i < 900000000]  # 9000000xx = fiches créées à la main
    for i in range(0, len(ids), 20):
        data = get_json("https://kitsu.io/api/edge/anime", None, params={
            "filter[id]": ",".join(map(str, ids[i:i + 20])), "include": "mappings",
            "fields[anime]": "id", "fields[mappings]": "externalSite,externalId,item", "page[limit]": 20,
        })
        maps = {m["id"]: m["attributes"] for m in data.get("included") or [] if m.get("type") == "mappings"}
        for a in data.get("data") or []:
            for rel in (a.get("relationships") or {}).get("mappings", {}).get("data") or []:
                attrs = maps.get(rel["id"]) or {}
                if attrs.get("externalSite") == "myanimelist/anime" and str(attrs.get("externalId", "")).isdigit():
                    out[int(a["id"])] = int(attrs["externalId"])
    return out
