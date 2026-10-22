"""Workers de fond : tout le travail lent (API externes) se fait ici, jamais pendant
une requête web. L'état est en base (colonnes *_state) → reprise propre après redémarrage.

- catalogue : séries → AnimeThemes (OP/ED) puis albums OST (Deezer)
- streaming : OP/ED → Deezer (version complète + ISRC) → Spotify ; albums OST → Spotify (UPC)
- youtube   : OP/ED (et pistes d'OST demandées) → vidéo YouTube
"""
import logging
import threading
import time
from datetime import timedelta

from sqlalchemy import or_, select

from . import animedb, config, deezer, library, match, spotify, youtube
from .db import FOUND, NONE, PENDING, Album, Franchise, PlaylistItem, Track, db_session, utcnow

log = logging.getLogger("music.workers")
_stop = threading.Event()
_cond = threading.Condition()
status = {"catalog": "", "streaming": "", "youtube": ""}


def wake():
    with _cond:
        _cond.notify_all()


def _pause(seconds: float):
    with _cond:
        _cond.wait(seconds)


def _loop(name: str, step):
    while not _stop.is_set():
        try:
            did = step()
        except Exception as e:  # une API en panne ne doit jamais tuer le worker
            log.exception("worker %s", name)
            status[name] = f"erreur : {e}"[:200]
            did = False
            _stop.wait(30)
        if not did:
            status[name] = ""
            _pause(20)


# --------------------------------------------------------------------------- catalogue
_claimed: set[int] = set()  # séries prises par un des workers catalogue (ils tournent à plusieurs)
_claim_lock = threading.Lock()


def _claim(q):
    with _claim_lock:
        f = (q.filter(Franchise.root_id.notin_(_claimed)) if _claimed else q).first()
        if f is not None:
            _claimed.add(f.root_id)
        return f


_last_all_sync = [0.0]


def _sync_all_users(s):
    """Toutes les 15 min : les listes de TOUS les membres du site anime (le blind test en a
    besoin même pour ceux qui n'ont jamais ouvert Anisongs)."""
    with _claim_lock:
        if time.time() - _last_all_sync[0] < 900:
            return
        _last_all_sync[0] = time.time()
    for uid in animedb.users_with_list():
        library.ensure_franchises(s, animedb.user_list(uid))


def catalog_step() -> bool:
    s = db_session()
    try:
        _sync_all_users(s)
        retry_before = utcnow() - timedelta(hours=1)
        f = (_claim(s.query(Franchise).filter(Franchise.at_state == PENDING).order_by(Franchise.updated_at))
             or _claim(s.query(Franchise).filter(Franchise.at_state == "error",
                                                 Franchise.at_synced_at < retry_before)))
        if f is not None:
            status["catalog"] = f"OP/ED : {f.title}"
            root, kind = f.root_id, "at"
        else:
            f = (_claim(s.query(Franchise).filter(Franchise.at_state == "done", Franchise.ost_state == PENDING))
                 or _claim(s.query(Franchise).filter(Franchise.ost_state == "error",
                                                     Franchise.ost_synced_at < retry_before)))
            if f is None:
                return False
            status["catalog"] = f"OST : {f.title}"
            root, kind = f.root_id, "ost"
    finally:
        s.close()
    try:
        (library.resolve_franchise if kind == "at" else library.resolve_ost)(root)
    finally:
        with _claim_lock:
            _claimed.discard(root)
    wake()  # de nouveaux titres attendent leurs liens
    return True


# --------------------------------------------------------------------------- streaming
def _priority(q):
    """Titres déjà dans une playlist d'abord (c'est ceux qu'on va exporter)."""
    in_pl = select(PlaylistItem.track_id)
    return q.order_by(Track.id.in_(in_pl).desc(), Track.id)


def _copy_from_twin(s, t: Track, fields: tuple[str, ...]) -> bool:
    """Même chanson (song_id) déjà résolue ailleurs (film récap, autre saison) → on recopie."""
    if not t.song_id:
        return False
    state = fields[0]
    twin = (s.query(Track).filter(Track.song_id == t.song_id, Track.id != t.id,
                                  getattr(Track, state) != PENDING).first())
    if twin is None:
        return False
    for fld in fields:
        setattr(t, fld, getattr(twin, fld))
    return True


def streaming_step() -> bool:
    s = db_session()
    try:
        # 1) Deezer : version complète + ISRC des OP/ED
        t = _priority(s.query(Track).filter(Track.album_id.is_(None), Track.deezer_state == PENDING)).first()
        if t is not None:
            status["streaming"] = f"Deezer : {t.title}"
            if not _copy_from_twin(s, t, ("deezer_state", "deezer_id", "deezer_preview", "isrc", "deezer_rank")):
                d = deezer.best_track(t.title, t.artist)
                if d:
                    full = deezer.track(d["id"]) or {}
                    t.deezer_id, t.deezer_preview = d["id"], d.get("preview") or ""
                    t.isrc, t.deezer_state = full.get("isrc") or "", FOUND
                    t.deezer_rank = full.get("rank") or d.get("rank") or 0
                else:
                    t.deezer_state = NONE
            s.commit()
            return True
        # 1 bis) popularité Deezer des titres trouvés avant qu'on la mesure (OP/ED d'abord)
        t = (s.query(Track).filter(Track.deezer_id.isnot(None), Track.deezer_rank.is_(None))
             .order_by(Track.album_id.isnot(None), Track.id).first())
        if t is not None:
            status["streaming"] = f"Popularité Deezer : {t.title}"
            full = deezer.track(t.deezer_id) or {}
            t.deezer_rank = full.get("rank") or 0
            if full.get("preview"):
                t.deezer_preview = full["preview"]  # liens d'extraits signés, ils expirent
            s.commit()
            return True
        if not config.spotify_configured():
            return False
        # 2) Spotify : OP/ED (ISRC si on l'a, sinon recherche texte)
        t = _priority(s.query(Track).filter(Track.album_id.is_(None), Track.spotify_state == PENDING)).first()
        if t is not None:
            status["streaming"] = f"Spotify : {t.title}"
            if not _copy_from_twin(s, t, ("spotify_state", "spotify_id")):
                sid = spotify.best_track(t.title, t.artist, t.isrc)
                t.spotify_id, t.spotify_state = sid, (FOUND if sid else NONE)
            s.commit()
            return True
        # 3) Spotify : albums d'OST par code-barres (UPC), piste par piste
        al = s.query(Album).filter(Album.spotify_state == PENDING).first()
        if al is not None:
            status["streaming"] = f"Spotify OST : {al.title}"
            al.spotify_id = spotify.album_by_upc(al.upc)
            if not al.spotify_id:  # pas d'UPC commun : recherche par titre d'album
                hits = spotify.search(f"{al.title} {al.artist}", kind="album", limit=5)
                for h in hits:
                    if match.title_sim(al.title, h.get("name") or "") >= 0.85:
                        al.spotify_id = h["id"]
                        break
            al.spotify_state = FOUND if al.spotify_id else NONE
            tracks = s.query(Track).filter(Track.album_id == al.id).order_by(Track.seq).all()
            sp_tracks = spotify.album_tracks(al.spotify_id) if al.spotify_id else []
            same_order = len(sp_tracks) == len(tracks)
            for i, t in enumerate(tracks):
                if t.spotify_state == "manual":
                    continue
                if same_order:
                    sp = sp_tracks[i]
                else:  # éditions différentes : on apparie par titre
                    sp = max(sp_tracks, default=None, key=lambda x: match.title_sim(t.title, x.get("name") or ""))
                    if sp and match.title_sim(t.title, sp.get("name") or "") < 0.8:
                        sp = None
                t.spotify_id = sp["id"] if sp else ""
                t.spotify_state = FOUND if sp else NONE
            s.commit()
            return True
        return False
    finally:
        s.close()


# --------------------------------------------------------------------------- youtube
def youtube_step() -> bool:
    if not config.YOUTUBE_SEARCH:
        return False
    s = db_session()
    try:
        t = _priority(s.query(Track).filter(
            Track.youtube_state == PENDING,
            or_(Track.album_id.is_(None), Track.youtube_wanted.is_(True)))).first()
        if t is not None:
            status["youtube"] = f"YouTube : {t.title}"
            if not _copy_from_twin(s, t, ("youtube_state", "youtube_id", "yt_views")):
                resolve_youtube(s, t)
            s.commit()
            return True
        # vues YouTube des vidéos trouvées avant qu'on les mesure (OP/ED/inserts)
        t = (s.query(Track).filter(Track.album_id.is_(None), Track.youtube_id != "",
                                   Track.yt_views.is_(None)).order_by(Track.id).first())
        if t is None:
            return False
        status["youtube"] = f"Vues YouTube : {t.title}"
        t.yt_views = youtube.video_views(t.title, t.artist)
        s.commit()
        return True
    finally:
        s.close()


def resolve_youtube(s, t: Track):
    context = ""
    if t.album_id:
        al = s.get(Album, t.album_id)
        f = s.get(Franchise, al.root_id) if al else None
        context = f.title if f else ""
    vid, views = youtube.best_video(t.title, t.artist, context)
    t.youtube_id, t.youtube_state = vid, (FOUND if vid else NONE)
    t.yt_views = views if vid else None


def start():
    if not config.WORKERS:
        return
    # AnimeThemes met ~5 s à répondre : 3 workers catalogue restent loin du quota (90/min)
    jobs = [("catalog", catalog_step)] * 3 + [("streaming", streaming_step), ("youtube", youtube_step)]
    for i, (name, step) in enumerate(jobs):
        threading.Thread(target=_loop, args=(name, step), name=f"worker-{name}-{i}", daemon=True).start()


def stop():
    _stop.set()
    wake()
