"""music.dwa.gold — les musiques (OP / ED / inserts / OST) de ta liste anime.

Connexion : SSO signé depuis anime.dwa.gold (même compte, rien à recréer).
Toutes les recherches lentes (AnimeThemes, Deezer, Spotify, YouTube) tournent dans les
workers de fond (`workers.py`) ; les routes web ne font que lire la base.
"""
import hashlib
import hmac
import html
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import animedb, config, library, spotify, workers
from .db import (FOUND, MANUAL, NONE, PENDING, Album, AtAnime, Franchise, Playlist, PlaylistItem,
                 SpotifyAuth, Track, UserState, db_session, id_list, init_db, utcnow)
from .net import ApiError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("music")

BASE_DIR = config.BASE_DIR
ASSET_V = str(int(time.time()))


@asynccontextmanager
async def lifespan(_app):
    init_db()
    workers.start()
    yield
    workers.stop()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=2000)
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET, session_cookie="music_session",
                   https_only=config.COOKIE_SECURE, same_site="lax", max_age=60 * 60 * 24 * 30)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://www.youtube.com https://s.ytimg.com",
    "style-src 'self' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data: https://media.kitsu.app https://media.kitsu.io https://s4.anilist.co "
    "https://cdn.discordapp.com https://*.dzcdn.net https://i.ytimg.com https://i.scdn.co "
    + config.ANIME_SITE_URL,
    # data: = son vide de 0 s qui « débloque » l'audio sur iPhone au 1er clic
    "media-src https://a.animethemes.moe https://v.animethemes.moe https://*.dzcdn.net data:",
    "frame-src https://www.youtube.com https://www.youtube-nocookie.com https://open.spotify.com",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# --------------------------------------------------------------------------- session
def current_uid(request: Request) -> str | None:
    uid = request.session.get("uid")
    if uid:
        return uid
    if config.LOCAL_USER_ID:  # dev : pas de SSO
        request.session["uid"] = config.LOCAL_USER_ID
        return config.LOCAL_USER_ID
    return None


def require_uid(request: Request) -> str:
    uid = current_uid(request)
    if not uid:
        raise HTTPException(401, "Connexion requise")
    return uid


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = request.session["csrf"] = secrets.token_hex(16)
    return tok


def check_csrf(request: Request):
    good = request.session.get("csrf")
    got = request.headers.get("x-csrf", "")
    if not good or not got or not secrets.compare_digest(good, got):
        raise HTTPException(403, "Jeton CSRF invalide, recharge la page")


async def body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "JSON invalide")
    if not isinstance(data, dict):
        raise HTTPException(400, "JSON invalide")
    return data


def _ids(v) -> list[int]:
    if not isinstance(v, list):
        raise HTTPException(400, "Liste d'ids attendue")
    return [int(x) for x in v[:5000] if isinstance(x, int) or (isinstance(x, str) and x.isdigit())]


# --------------------------------------------------------------------------- pages
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    uid = current_uid(request)
    if not uid:
        return templates.TemplateResponse(request, "login.html", {"v": ASSET_V, "anime_url": config.ANIME_SITE_URL})
    user = animedb.get_user(uid)
    if user is None:  # compte supprimé côté site anime
        request.session.clear()
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "app.html", {
        "v": ASSET_V, "user": user, "csrf": csrf_token(request), "anime_url": config.ANIME_SITE_URL,
    })


@app.get("/login")
def login(request: Request):
    if current_uid(request):
        return RedirectResponse("/", status_code=303)
    if not config.SSO_SECRET:
        return HTMLResponse("<h1>SSO non configuré</h1>", status_code=503)
    state = secrets.token_urlsafe(24)
    request.session["sso_state"] = state
    return RedirectResponse(f"{config.ANIME_SITE_URL}/sso/music?state={state}", status_code=303)


def sso_signature(uid: str, ts: str, state: str) -> str:
    msg = f"music|{uid}|{ts}|{state}".encode()
    return hmac.new(config.SSO_SECRET.encode(), msg, hashlib.sha256).hexdigest()


@app.get("/auth/sso")
def auth_sso(request: Request, uid: str = "", ts: str = "", state: str = "", sig: str = ""):
    expected_state = request.session.pop("sso_state", None)
    if not (config.SSO_SECRET and uid and ts.isdigit() and state and sig and expected_state):
        raise HTTPException(400, "Connexion invalide, réessaie")
    if not secrets.compare_digest(state, expected_state):
        raise HTTPException(400, "Connexion invalide (état)")
    if abs(time.time() - int(ts)) > 300:
        raise HTTPException(400, "Lien de connexion expiré, réessaie")
    if not hmac.compare_digest(sso_signature(uid, ts, state), sig):
        raise HTTPException(403, "Signature invalide")
    if animedb.get_user(uid) is None:
        raise HTTPException(403, "Compte inconnu")
    request.session.clear()
    request.session["uid"] = uid
    request.session["csrf"] = secrets.token_hex(16)
    return RedirectResponse("/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# --------------------------------------------------------------------------- bibliothèque
SEASON_ORDER = {"Winter": 0, "Spring": 1, "Summer": 2, "Fall": 3}
KIND_ORDER = {"OP": 0, "ED": 1, "IN": 2, "OST": 3}


def track_json(t) -> dict:
    """Format compact (des milliers de pistes d'OST) : champs vides omis, sauf les états
    de recherche ss / ys dont la valeur "" veut dire « en attente »."""
    d = {
        "id": t.id, "k": t.kind, "s": t.slug, "t": t.title, "a": t.artist, "ep": t.episodes,
        "au": t.at_audio, "v": t.at_video, "sp": t.spotify_id, "yt": t.youtube_id,
        "pv": t.deezer_preview, "d": t.duration, "sg": t.song_id, "spl": bool(t.spoiler),
    }
    d = {k: v for k, v in d.items() if v not in ("", None, 0, False) or k == "id"}
    d["ss"], d["ys"] = t.spotify_state or "", t.youtube_state or ""
    return d


# Colonnes lues pour la bibliothèque : des tuples plutôt que des objets ORM (des milliers
# de pistes d'OST → 3 à 4 fois plus rapide sur le Pi).
TRACK_COLS = (Track.id, Track.kind, Track.slug, Track.title, Track.artist, Track.episodes,
              Track.at_audio, Track.at_video, Track.spotify_id, Track.youtube_id, Track.deezer_preview,
              Track.duration, Track.song_id, Track.spoiler, Track.spotify_state, Track.youtube_state,
              Track.at_anime_id, Track.album_id, Track.seq)


def build_library(s, uid: str) -> dict:
    entries = animedb.user_list(uid)
    franchises = library.ensure_franchises(s, entries)
    at_ids = {i for f in franchises.values() for i in id_list(f.at_ids)}
    at_rows = {a.id: a for a in s.query(AtAnime).filter(AtAnime.id.in_(at_ids))} if at_ids else {}
    by_at: dict[int, list] = {}
    if at_ids:
        for t in s.query(*TRACK_COLS).filter(Track.at_anime_id.in_(at_ids)):
            by_at.setdefault(t.at_anime_id, []).append(t)
    roots = list(franchises)
    albums = s.query(Album).filter(Album.root_id.in_(roots)).order_by(Album.release_date).all() if roots else []
    by_album: dict[int, list] = {}
    if albums:
        for t in s.query(*TRACK_COLS).filter(Track.album_id.in_([a.id for a in albums])).order_by(Track.seq):
            by_album.setdefault(t.album_id, []).append(t)
    albums_by_root: dict[int, list[Album]] = {}
    for a in albums:
        albums_by_root.setdefault(a.root_id, []).append(a)

    out = []
    for e in entries:
        f = franchises[e["root_id"]]
        seasons = []
        for aid in id_list(f.at_ids):
            a = at_rows.get(aid)
            if a is None:
                continue
            tracks = sorted(by_at.get(aid, []), key=lambda t: (KIND_ORDER.get(t.kind, 9), t.seq or 0, t.id))
            if tracks:
                seasons.append({"id": a.id, "name": a.name, "year": a.year, "season": a.season,
                                "format": a.media_format, "tracks": [track_json(t) for t in tracks]})
        seasons.sort(key=lambda x: (x["year"] or 9999, SEASON_ORDER.get(x["season"], 5), x["name"]))
        out.append({
            "id": e["root_id"], "title": e["title"], "english": e["english"], "cover": e["cover"],
            "status": e["status"], "status_label": animedb.STATUS_LABELS.get(e["status"], e["status"]),
            "rating": e["rating"], "updated": e["updated_at"],
            "at_state": f.at_state, "ost_state": f.ost_state,
            "seasons": seasons,
            "albums": [{"id": a.id, "title": a.title, "artist": a.artist, "cover": a.cover,
                        "year": (a.release_date or "")[:4], "sp": a.spotify_id,
                        "tracks": [track_json(t) for t in by_album.get(a.id, [])]}
                       for a in albums_by_root.get(f.root_id, [])],
        })
    return {"anime": out}


def progress(s, uid: str) -> dict:
    entries = animedb.user_list(uid)
    roots = [e["root_id"] for e in entries]
    frs = s.query(Franchise).filter(Franchise.root_id.in_(roots)).all() if roots else []
    at_ids = {i for f in frs for i in id_list(f.at_ids)}
    album_ids = [a.id for a in s.query(Album.id).filter(Album.root_id.in_(roots))] if roots else []
    q = s.query(Track).filter((Track.at_anime_id.in_(at_ids)) | (Track.album_id.in_(album_ids)))
    rows = q.with_entities(Track.kind, Track.deezer_state, Track.spotify_state, Track.youtube_state,
                           Track.youtube_wanted).all()
    themes = [r for r in rows if r.kind != "OST"]
    p = {
        "series": len(frs),
        "at_pending": sum(1 for f in frs if f.at_state == PENDING),
        "ost_pending": sum(1 for f in frs if f.ost_state == PENDING),
        "series_pending": sum(1 for f in frs if f.at_state == PENDING or f.ost_state == PENDING),
        "themes": len(themes), "ost": len(rows) - len(themes),
        "deezer_pending": sum(1 for r in themes if r.deezer_state == PENDING),
        "spotify_found": sum(1 for r in rows if r.spotify_state in (FOUND, MANUAL)),
        "spotify_pending": sum(1 for r in rows if r.spotify_state == PENDING) if config.spotify_configured() else 0,
        "youtube_found": sum(1 for r in rows if r.youtube_state in (FOUND, MANUAL)),
        "youtube_pending": sum(1 for r in rows if r.youtube_state == PENDING
                               and (r.kind != "OST" or r.youtube_wanted)) if config.YOUTUBE_SEARCH else 0,
        "workers": {k: v for k, v in workers.status.items() if v},
    }
    p["busy"] = bool(p["series_pending"] or p["deezer_pending"] or p["spotify_pending"] or p["youtube_pending"])
    return p


@app.get("/api/library")
def api_library(request: Request):
    uid = require_uid(request)
    s = db_session()
    try:
        data = build_library(s, uid)
        data["progress"] = progress(s, uid)
        data["spotify"] = spotify_status(s, uid)
        data.update(playlists_payload(s, uid, data))
        workers.wake()
        # JSONResponse direct : évite le jsonable_encoder de FastAPI, lent sur ~6000 pistes
        return JSONResponse(data)
    finally:
        s.close()


@app.get("/api/progress")
def api_progress(request: Request):
    uid = require_uid(request)
    s = db_session()
    try:
        return progress(s, uid)
    finally:
        s.close()


@app.post("/api/sync")
def api_sync(request: Request):
    """« Rafraîchir » : re-vérifie toutes les séries (nouveaux OP, nouvelles saisons)."""
    uid = require_uid(request)
    check_csrf(request)
    s = db_session()
    try:
        roots = [e["root_id"] for e in animedb.user_list(uid)]
        if roots:
            s.query(Franchise).filter(Franchise.root_id.in_(roots), Franchise.at_state != PENDING) \
                .update({Franchise.at_state: PENDING}, synchronize_session=False)
            s.query(Franchise).filter(Franchise.root_id.in_(roots), Franchise.ost_state == "error") \
                .update({Franchise.ost_state: PENDING}, synchronize_session=False)
            s.commit()
        workers.wake()
        return {"ok": True}
    finally:
        s.close()


# --------------------------------------------------------------------------- titres
SPOTIFY_ID = re.compile(r"(?:open\.spotify\.com/(?:intl-[a-z]+/)?track/|spotify:track:)?([A-Za-z0-9]{22})")
YOUTUBE_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)?([A-Za-z0-9_-]{11})(?:[?&#].*)?$")


@app.post("/api/tracks/{tid}/links")
async def api_track_links(request: Request, tid: int):
    """Corriger un lien à la main (colle un lien Spotify / YouTube, ou vide pour « aucun »)."""
    require_uid(request)
    check_csrf(request)
    data = await body(request)
    s = db_session()
    try:
        t = s.get(Track, tid)
        if t is None:
            raise HTTPException(404, "Titre inconnu")
        if "spotify" in data:
            raw = str(data["spotify"] or "").strip()
            m = SPOTIFY_ID.search(raw) if raw else None
            if raw and not m:
                raise HTTPException(400, "Lien Spotify non reconnu")
            t.spotify_id, t.spotify_state = (m.group(1), MANUAL) if m else ("", NONE)
        if "youtube" in data:
            raw = str(data["youtube"] or "").strip()
            m = YOUTUBE_ID.search(raw) if raw else None
            if raw and not m:
                raise HTTPException(400, "Lien YouTube non reconnu")
            t.youtube_id, t.youtube_state = (m.group(1), MANUAL) if m else ("", NONE)
        s.commit()
        return {"ok": True, "track": track_json(t)}
    finally:
        s.close()


@app.post("/api/tracks/{tid}/youtube")
async def api_track_youtube(request: Request, tid: int):
    """Le lecteur veut la version complète d'un titre pas encore cherché : on cherche tout de suite."""
    require_uid(request)
    check_csrf(request)

    def work():
        s = db_session()
        try:
            t = s.get(Track, tid)
            if t is None:
                raise HTTPException(404, "Titre inconnu")
            if t.youtube_state == PENDING and config.YOUTUBE_SEARCH:
                t.youtube_wanted = True
                workers.resolve_youtube(s, t)
                s.commit()
            return {"yt": t.youtube_id, "ys": t.youtube_state}
        finally:
            s.close()

    return await run_in_threadpool(work)


# --------------------------------------------------------------------------- playlists
def _owned(s, uid: str, pid: int) -> Playlist:
    pl = s.get(Playlist, pid)
    if pl is None or pl.user_id != uid:
        raise HTTPException(404, "Playlist introuvable")
    return pl


def _ensure_default(s, uid: str) -> UserState:
    st = s.get(UserState, uid)
    if st is None:
        st = UserState(user_id=uid)
        s.add(st)
    if not s.query(Playlist).filter_by(user_id=uid).count():
        pl = Playlist(user_id=uid, name="Ma playlist anime", position=0)
        s.add(pl)
        s.flush()
        st.active_playlist = pl.id
    st.last_seen = utcnow()
    s.commit()
    return st


def playlists_payload(s, uid: str, library_data: dict | None = None) -> dict:
    st = _ensure_default(s, uid)
    pls = s.query(Playlist).filter_by(user_id=uid).order_by(Playlist.position, Playlist.id).all()
    items = s.query(PlaylistItem).filter(PlaylistItem.playlist_id.in_([p.id for p in pls])) \
        .order_by(PlaylistItem.pos, PlaylistItem.id).all()
    by_pl: dict[int, list[int]] = {}
    for it in items:
        by_pl.setdefault(it.playlist_id, []).append(it.track_id)
    if st.active_playlist not in {p.id for p in pls}:
        st.active_playlist = pls[0].id
        s.commit()
    # titres d'une playlist qui ne sont plus dans la bibliothèque (anime retiré de la liste)
    extra = {}
    if library_data is not None:
        known = {t["id"] for a in library_data["anime"] for se in a["seasons"] for t in se["tracks"]}
        known |= {t["id"] for a in library_data["anime"] for al in a["albums"] for t in al["tracks"]}
        missing = {tid for ids in by_pl.values() for tid in ids} - known
        if missing:
            extra = {t.id: track_json(t) for t in s.query(Track).filter(Track.id.in_(missing))}
    return {
        "playlists": [{"id": p.id, "name": p.name, "spotify_id": p.spotify_id,
                       "items": by_pl.get(p.id, [])} for p in pls],
        "active": st.active_playlist,
        "extra": extra,
    }


@app.get("/api/playlists")
def api_playlists(request: Request):
    uid = require_uid(request)
    s = db_session()
    try:
        return playlists_payload(s, uid)
    finally:
        s.close()


@app.post("/api/playlists")
async def api_playlist_create(request: Request):
    uid = require_uid(request)
    check_csrf(request)
    data = await body(request)
    name = str(data.get("name") or "").strip()[:80] or "Nouvelle playlist"
    s = db_session()
    try:
        if s.query(Playlist).filter_by(user_id=uid).count() >= 50:
            raise HTTPException(400, "50 playlists maximum")
        pos = (s.query(func.max(Playlist.position)).filter_by(user_id=uid).scalar() or 0) + 1
        pl = Playlist(user_id=uid, name=name, position=pos)
        s.add(pl)
        s.flush()
        st = _ensure_default(s, uid)
        st.active_playlist = pl.id
        s.commit()
        return playlists_payload(s, uid)
    finally:
        s.close()


@app.post("/api/playlists/{pid}/rename")
async def api_playlist_rename(request: Request, pid: int):
    uid = require_uid(request)
    check_csrf(request)
    data = await body(request)
    s = db_session()
    try:
        pl = _owned(s, uid, pid)
        pl.name = str(data.get("name") or "").strip()[:80] or pl.name
        s.commit()
        return playlists_payload(s, uid)
    finally:
        s.close()


@app.post("/api/playlists/{pid}/delete")
def api_playlist_delete(request: Request, pid: int):
    uid = require_uid(request)
    check_csrf(request)
    s = db_session()
    try:
        pl = _owned(s, uid, pid)
        s.query(PlaylistItem).filter_by(playlist_id=pl.id).delete()
        s.delete(pl)
        s.commit()
        return playlists_payload(s, uid)
    finally:
        s.close()


@app.post("/api/active")
async def api_active(request: Request):
    uid = require_uid(request)
    check_csrf(request)
    data = await body(request)
    s = db_session()
    try:
        pl = _owned(s, uid, int(data.get("id") or 0))
        st = _ensure_default(s, uid)
        st.active_playlist = pl.id
        s.commit()
        return {"ok": True, "active": pl.id}
    finally:
        s.close()


@app.post("/api/playlists/{pid}/add")
async def api_playlist_add(request: Request, pid: int):
    uid = require_uid(request)
    check_csrf(request)
    ids = _ids((await body(request)).get("ids"))
    s = db_session()
    try:
        pl = _owned(s, uid, pid)
        valid = {t.id for t in s.query(Track.id).filter(Track.id.in_(ids))} if ids else set()
        have = {it.track_id for it in s.query(PlaylistItem.track_id).filter_by(playlist_id=pl.id)}
        pos = s.query(func.max(PlaylistItem.pos)).filter_by(playlist_id=pl.id).scalar() or 0
        added = 0
        for tid in dict.fromkeys(ids):
            if tid in valid and tid not in have:
                pos += 1
                s.add(PlaylistItem(playlist_id=pl.id, track_id=tid, pos=pos))
                have.add(tid)
                added += 1
        if len(have) > 5000:
            raise HTTPException(400, "5000 titres maximum par playlist")
        pl.updated_at = utcnow()
        s.commit()
        workers.wake()  # les titres de playlist passent en tête pour les liens
        return {"ok": True, "added": added, "items": _items(s, pl.id)}
    finally:
        s.close()


def _items(s, pid: int) -> list[int]:
    return [it.track_id for it in s.query(PlaylistItem).filter_by(playlist_id=pid)
            .order_by(PlaylistItem.pos, PlaylistItem.id)]


@app.post("/api/playlists/{pid}/remove")
async def api_playlist_remove(request: Request, pid: int):
    uid = require_uid(request)
    check_csrf(request)
    ids = _ids((await body(request)).get("ids"))
    s = db_session()
    try:
        pl = _owned(s, uid, pid)
        if ids:
            s.query(PlaylistItem).filter(PlaylistItem.playlist_id == pl.id,
                                         PlaylistItem.track_id.in_(ids)).delete(synchronize_session=False)
        s.commit()
        return {"ok": True, "items": _items(s, pl.id)}
    finally:
        s.close()


@app.post("/api/playlists/{pid}/order")
async def api_playlist_order(request: Request, pid: int):
    uid = require_uid(request)
    check_csrf(request)
    ids = _ids((await body(request)).get("ids"))
    s = db_session()
    try:
        pl = _owned(s, uid, pid)
        rank = {tid: i for i, tid in enumerate(ids)}
        rows = s.query(PlaylistItem).filter_by(playlist_id=pl.id).all()
        tail = len(ids)
        for it in sorted(rows, key=lambda r: r.pos):
            if it.track_id in rank:
                it.pos = rank[it.track_id]
            else:
                tail += 1
                it.pos = tail
        s.commit()
        return {"ok": True, "items": _items(s, pl.id)}
    finally:
        s.close()


def _anime_names(s) -> dict:
    """Pour l'export texte : titre de série d'un titre (via sa saison ou son album)."""
    names = {}
    for f in s.query(Franchise):
        for aid in id_list(f.at_ids):
            names[("at", aid)] = f.title
        names[("root", f.root_id)] = f.title
    for a in s.query(Album):
        names[("al", a.id)] = names.get(("root", a.root_id), "")
    return names


@app.get("/api/playlists/{pid}/export.txt")
def api_playlist_export(request: Request, pid: int):
    uid = require_uid(request)
    s = db_session()
    try:
        pl = _owned(s, uid, pid)
        ids = _items(s, pl.id)
        tracks = {t.id: t for t in s.query(Track).filter(Track.id.in_(ids))} if ids else {}
        names = _anime_names(s)
        lines = [f"# {pl.name} — {len(ids)} titres (music.dwa.gold)", ""]
        for tid in ids:
            t = tracks.get(tid)
            if t is None:
                continue
            anime = names.get(("at", t.at_anime_id)) or names.get(("al", t.album_id)) or ""
            links = []
            if t.spotify_id:
                links.append(f"https://open.spotify.com/track/{t.spotify_id}")
            if t.youtube_id:
                links.append(f"https://youtu.be/{t.youtube_id}")
            lines.append(f"{anime} [{t.slug or t.kind}] {t.title} — {t.artist}" + (
                "  |  " + "  ".join(links) if links else ""))
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", pl.name)[:40] or "playlist"
        return PlainTextResponse("\n".join(lines) + "\n", headers={
            "Content-Disposition": f'attachment; filename="{safe}.txt"'})
    finally:
        s.close()


# --------------------------------------------------------------------------- Spotify
def spotify_status(s, uid: str) -> dict:
    a = s.get(SpotifyAuth, uid)
    return {"configured": config.spotify_configured(),
            "connected": bool(a and a.refresh_token),
            "name": (a.display_name if a else "") or ""}


def _user_token(s, uid: str) -> str:
    a = s.get(SpotifyAuth, uid)
    if not a or not a.refresh_token:
        raise HTTPException(400, "Connecte d'abord ton compte Spotify")
    if a.expires_at < time.time() + 60:
        try:
            d = spotify.refresh(a.refresh_token)
        except ApiError:
            a.refresh_token = ""
            s.commit()
            raise HTTPException(400, "Connexion Spotify expirée, reconnecte-toi")
        a.access_token = d["access_token"]
        a.refresh_token = d.get("refresh_token") or a.refresh_token
        a.expires_at = time.time() + d.get("expires_in", 3600)
        s.commit()
    return a.access_token


@app.get("/spotify/login")
def spotify_login(request: Request):
    require_uid(request)
    if not config.spotify_configured():
        raise HTTPException(503, "Spotify n'est pas configuré sur le serveur")
    state = secrets.token_urlsafe(24)
    request.session["sp_state"] = state
    return RedirectResponse(spotify.authorize_url(state), status_code=303)


@app.get("/spotify/callback")
def spotify_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    uid = require_uid(request)
    expected = request.session.pop("sp_state", None)
    if error:
        return RedirectResponse("/?spotify=refus", status_code=303)
    if not code or not expected or not secrets.compare_digest(state, expected):
        raise HTTPException(400, "État OAuth invalide")
    d = spotify.exchange_code(code)
    who = spotify.me(d["access_token"])
    s = db_session()
    try:
        a = s.get(SpotifyAuth, uid) or SpotifyAuth(user_id=uid)
        a.access_token, a.refresh_token = d["access_token"], d.get("refresh_token") or ""
        a.expires_at = time.time() + d.get("expires_in", 3600)
        a.spotify_user, a.display_name = who.get("id") or "", who.get("display_name") or who.get("id") or ""
        s.merge(a)
        s.commit()
    finally:
        s.close()
    return RedirectResponse("/?spotify=ok", status_code=303)


@app.post("/spotify/logout")
def spotify_logout(request: Request):
    uid = require_uid(request)
    check_csrf(request)
    s = db_session()
    try:
        a = s.get(SpotifyAuth, uid)
        if a:
            s.delete(a)
            s.commit()
        return {"ok": True}
    finally:
        s.close()


@app.post("/api/playlists/{pid}/spotify")
async def api_playlist_to_spotify(request: Request, pid: int):
    """Crée (ou met à jour) la playlist sur le compte Spotify du membre."""
    uid = require_uid(request)
    check_csrf(request)

    def work():
        s = db_session()
        try:
            pl = _owned(s, uid, pid)
            ids = _items(s, pl.id)
            tracks = {t.id: t for t in s.query(Track).filter(Track.id.in_(ids))} if ids else {}
            uris = list(dict.fromkeys(f"spotify:track:{tracks[i].spotify_id}" for i in ids
                                      if i in tracks and tracks[i].spotify_id))
            if not uris:
                raise HTTPException(400, "Aucun titre de cette playlist n'est (encore) trouvé sur Spotify")
            token = _user_token(s, uid)
            desc = "Musiques d'anime de ma liste — faite avec music.dwa.gold"
            try:
                if not pl.spotify_id:
                    raise ApiError("nouvelle")
                spotify.set_playlist_items(token, pl.spotify_id, uris)
            except ApiError:  # jamais créée, ou supprimée côté Spotify : on (re)crée
                pl.spotify_id = spotify.create_playlist(token, pl.name, desc)["id"]
                s.commit()
                spotify.set_playlist_items(token, pl.spotify_id, uris)
            s.commit()
            return {"ok": True, "url": f"https://open.spotify.com/playlist/{pl.spotify_id}",
                    "count": len(uris), "missing": len(ids) - len(uris)}
        except ApiError as e:
            log.warning("export Spotify : %s", e)
            raise HTTPException(502, f"Spotify a refusé : {e}"[:300])
        finally:
            s.close()

    return await run_in_threadpool(work)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/") or request.method != "GET":
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>Erreur</title>"
                        f"<link rel=stylesheet href='/static/app.css?v={ASSET_V}'><body class=login-page>"
                        f"<main class=login-card><h1>{exc.status_code}</h1><p>{html.escape(str(exc.detail))}</p>"
                        f"<a class='btn accent' href='/'>Retour</a></main>",
                        status_code=exc.status_code)
