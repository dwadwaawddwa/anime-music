"""Client Spotify Web API (optionnel, nécessite une app developer.spotify.com).

- Jeton « application » (client credentials) : recherche des titres / albums.
- Jeton « utilisateur » (OAuth) : création de la playlist sur le compte du membre.
Règles du mode développement 2026 : recherche limitée à 10 résultats, création via
POST /me/playlists, ajout via /playlists/{id}/items.
"""
import base64
import time
import urllib.parse

from . import config, match
from .net import ApiError, Throttle, get_json, request

API = "https://api.spotify.com/v1"
ACCOUNTS = "https://accounts.spotify.com"
SCOPES = "playlist-modify-private playlist-modify-public"
throttle = Throttle(0.35)

_app_token = {"value": "", "exp": 0.0}


def _basic() -> str:
    raw = f"{config.SPOTIFY_CLIENT_ID}:{config.SPOTIFY_CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def app_token() -> str:
    if _app_token["value"] and _app_token["exp"] > time.time() + 60:
        return _app_token["value"]
    r = request("POST", f"{ACCOUNTS}/api/token", data={"grant_type": "client_credentials"},
                headers={"Authorization": _basic()})
    if r.status_code != 200:
        raise ApiError(f"Spotify token: {r.status_code} {r.text[:200]}")
    d = r.json()
    _app_token.update(value=d["access_token"], exp=time.time() + d.get("expires_in", 3600))
    return _app_token["value"]


def _api(path: str, **params):
    return get_json(API + path, throttle, params=params,
                    headers={"Authorization": f"Bearer {app_token()}"})


def search(q: str, kind: str = "track", limit: int = 10) -> list[dict]:
    d = _api("/search", q=q, type=kind, limit=min(limit, 10), market=config.SPOTIFY_MARKET)
    return ((d.get(kind + "s") or {}).get("items")) or []


def _score(c: dict, title: str, artist: str) -> float:
    ts = match.title_sim(title, c.get("name") or "")
    ar = max((match.artist_sim(artist, a.get("name") or "") for a in c.get("artists") or []), default=0)
    if ts < 0.8 or ar < 0.5:
        return 0
    return ts * 0.6 + ar * 0.4 - match.penalty(c.get("name") or "", title)


def best_track(title: str, artist: str, isrc: str = "") -> str:
    """Id Spotify du titre, ou "" si introuvable."""
    if isrc:
        items = [c for c in search(f"isrc:{isrc}", limit=5) if c.get("id")]
        if items:
            return items[0]["id"]
    first_artist = (match.split_artists(artist) or [artist])[0]
    for q in (f'track:"{title}" artist:"{first_artist}"', f"{title} {first_artist}"):
        scored = [(_score(c, title, artist), c) for c in search(q)]
        scored = [s for s in scored if s[0] >= 0.7]
        if scored:
            return max(scored, key=lambda s: s[0])[1]["id"]
    return ""


def album_by_upc(upc: str) -> str:
    if not upc:
        return ""
    items = search(f"upc:{upc}", kind="album", limit=3)
    return items[0]["id"] if items else ""


def album_tracks(album_id: str) -> list[dict]:
    out, offset = [], 0
    while True:
        d = _api(f"/albums/{album_id}/tracks", limit=50, offset=offset, market=config.SPOTIFY_MARKET)
        out += d.get("items") or []
        if not d.get("next"):
            return out
        offset += 50


# --------------------------------------------------------------------------- OAuth membre
def authorize_url(state: str) -> str:
    return f"{ACCOUNTS}/authorize?" + urllib.parse.urlencode({
        "client_id": config.SPOTIFY_CLIENT_ID, "response_type": "code",
        "redirect_uri": config.SPOTIFY_REDIRECT_URI, "scope": SCOPES, "state": state,
    })


def _token_call(data: dict) -> dict:
    r = request("POST", f"{ACCOUNTS}/api/token", data=data, headers={"Authorization": _basic()})
    if r.status_code != 200:
        raise ApiError(f"Spotify OAuth: {r.status_code} {r.text[:200]}")
    return r.json()


def exchange_code(code: str) -> dict:
    return _token_call({"grant_type": "authorization_code", "code": code,
                        "redirect_uri": config.SPOTIFY_REDIRECT_URI})


def refresh(refresh_token: str) -> dict:
    return _token_call({"grant_type": "refresh_token", "refresh_token": refresh_token})


def _user_call(token: str, method: str, path: str, json=None):
    r = request(method, API + path, throttle, json=json, headers={"Authorization": f"Bearer {token}"})
    if r.status_code >= 400:
        raise ApiError(f"Spotify {method} {path}: {r.status_code} {r.text[:300]}")
    return r.json() if r.content else {}


def me(token: str) -> dict:
    return _user_call(token, "GET", "/me")


def create_playlist(token: str, name: str, description: str) -> dict:
    return _user_call(token, "POST", "/me/playlists",
                      {"name": name, "description": description, "public": False})


def set_playlist_items(token: str, playlist_id: str, uris: list[str]):
    """Remplace tout le contenu (100 max par appel : PUT le 1er lot, POST les suivants)."""
    _user_call(token, "PUT", f"/playlists/{playlist_id}/items", {"uris": uris[:100]})
    for i in range(100, len(uris), 100):
        _user_call(token, "POST", f"/playlists/{playlist_id}/items", {"uris": uris[i:i + 100]})
