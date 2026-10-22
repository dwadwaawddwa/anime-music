"""Rapprochement liste anime ↔ AnimeThemes / AnisongDB / Deezer, et stockage des titres."""
import logging
from datetime import timedelta

from . import animethemes as at
from . import anisongdb, config, deezer, grouping, match
from .db import (FOUND, MANUAL, NONE, PENDING, Album, AtAnime, Franchise, PlaylistItem, Track,
                 db_session, id_list, id_str, utcnow)

log = logging.getLogger("music.library")


def ensure_franchises(session, entries: list[dict]) -> dict[int, Franchise]:
    """Crée / met à jour les séries d'une liste. Une série nouvelle, dont les saisons
    connues ont changé, ou trop ancienne repasse « à faire » pour les workers."""
    roots = [e["root_id"] for e in entries]
    have = {f.root_id: f for f in session.query(Franchise).filter(Franchise.root_id.in_(roots))} if roots else {}
    stale_before = utcnow() - timedelta(days=config.FRANCHISE_TTL_DAYS)
    for e in entries:
        f = have.get(e["root_id"])
        if f is None:
            f = Franchise(root_id=e["root_id"])
            session.add(f)
            have[e["root_id"]] = f
        members = id_str(e["members"])
        if f.member_ids != members:
            f.member_ids = members
            f.at_state = PENDING
        f.title, f.english, f.native = e["title"], e["english"], e["native"]
        f.cover_url, f.group_key, f.year = e["cover"], e["group_key"], e["year"] or 0
        synced = f.at_synced_at
        if synced is not None and synced.tzinfo is None:
            synced = synced.replace(tzinfo=stale_before.tzinfo)
        if f.at_state == "done" and synced is not None and synced < stale_before:
            f.at_state = PENDING  # nouvelles saisons / nouveaux OP depuis la dernière fois
    session.commit()
    return have


def _same_franchise(keys: set[str], anime: dict) -> bool:
    return grouping.normalize(anime.get("name") or "") in keys


def resolve_franchise(root_id: int):
    """Trouve toutes les saisons / films d'une série (AnimeThemes, complété par AnisongDB)
    et enregistre leurs OP / ED / inserts."""
    s = db_session()
    try:
        f = s.get(Franchise, root_id)
        if f is None:
            return
        members = id_list(f.member_ids)
        keys = {k for k in (f.group_key, grouping.normalize(f.title)) if k}
        light = at.by_kitsu(members)
        ids = {a["id"] for a in light}
        keys |= {grouping.normalize(a["name"]) for a in light}
        series = {sr["slug"] for a in light for sr in a.get("series") or []}
        if not ids:  # pas de lien Kitsu côté AnimeThemes : recherche par titre
            ekeys = keys | {grouping.normalize(f.english)} - {""}
            for q in dict.fromkeys(x for x in (f.title, f.english) if x):
                for a in at.search(q):
                    if any(grouping.normalize(n) in ekeys for n in at.names(a)):
                        ids.add(a["id"])
                        series |= {sr["slug"] for sr in a.get("series") or []}
        for slug in series:  # autres saisons / films de la même série
            for a in at.series_anime(slug):
                if a["id"] not in ids and _same_franchise(keys, a):
                    ids.add(a["id"])
        full = at.by_ids(sorted(ids)) if ids else []

        # AnisongDB : artistes manquants, inserts, et saisons / films absents d'AnimeThemes
        # (ex. la plupart des films One Piece) : ids MAL de nos fiches Kitsu non couvertes.
        mal_of = {a["id"]: at.external_id(a, "MyAnimeList") for a in full}
        at_mals = {m for m in mal_of.values() if m}
        try:
            member_mals = set(anisongdb.kitsu_to_mal(members).values())
        except Exception as e:
            log.warning("Kitsu mappings %s : %s", f.title, e)
            member_mals = set()
        orphan_mals = member_mals - at_mals
        mal_ids = at_mals | orphan_mals
        try:
            songs = anisongdb.by_mal(sorted(mal_ids)) if mal_ids else []
        except Exception as e:  # AnisongDB en panne : AnimeThemes seul, on réessaiera
            log.warning("AnisongDB %s : %s", f.title, e)
            songs = []
        at_ids = set()
        taken: set[int] = set()  # une chanson AnisongDB ne va que dans une seule fiche
        for a in full:
            mal = mal_of[a["id"]]
            mine = [x for x in songs if mal and x["mal"] == mal and x["ann_song_id"] not in taken]
            taken |= {x["ann_song_id"] for x in mine}
            _store_anime(s, a, mine)
            at_ids.add(a["id"])
        for mal in orphan_mals:
            mine = [x for x in songs if x["mal"] == mal]
            if mine:
                _store_asdb_anime(s, mal, mine)
                at_ids.add(-mal)
        f.at_ids = id_str(at_ids)
        f.at_state = "done"
        f.at_synced_at = utcnow()
        s.commit()
        log.info("série %s « %s » : %d fiches", root_id, f.title, len(at_ids))
    except Exception:
        s.rollback()
        f = s.get(Franchise, root_id)
        if f is not None:
            f.at_state = "error"
            f.at_synced_at = utcnow()
            s.commit()
        raise
    finally:
        s.close()


def _upsert(s, key: str, existing: dict) -> Track:
    t = existing.get(key)
    if t is None:
        t = Track(key=key)
        s.add(t)
        existing[key] = t
    return t


def _set_artist(t: Track, artist: str):
    """Artiste trouvé après coup : on relance les recherches ratées faute d'artiste."""
    if artist and t.artist != artist:
        t.artist = artist
        for state in ("deezer_state", "spotify_state", "youtube_state"):
            if getattr(t, state) == NONE:
                setattr(t, state, PENDING)


def _store_anime(s, a: dict, asdb_songs: list[dict]):
    row = s.get(AtAnime, a["id"]) or AtAnime(id=a["id"])
    row.slug, row.name = a.get("slug") or "", a.get("name") or ""
    row.year, row.season = a.get("year") or 0, a.get("season") or ""
    row.media_format, row.fetched_at = a.get("media_format") or "", utcnow()
    row.mal_id = at.external_id(a, "MyAnimeList")
    s.merge(row)
    themes = at.themes(a)
    keys = [t["key"] for t in themes] + [f"asdb:{x['ann_song_id']}" for x in asdb_songs]
    existing = {t.key: t for t in s.query(Track).filter(Track.key.in_(keys))} if keys else {}
    stored = []
    for th in themes:
        t = _upsert(s, th["key"], existing)
        t.kind, t.at_anime_id, t.song_id = th["kind"], a["id"], th["song_id"]
        t.seq, t.slug, t.title = th["seq"], th["slug"], th["title"]
        t.episodes, t.at_audio, t.at_video = th["episodes"], th["at_audio"], th["at_video"]
        t.spoiler, t.nsfw = th["spoiler"], th["nsfw"]
        t.at_plays = th["plays"]
        _set_artist(t, th["artist"])
        # liens saisis par la communauté AnimeThemes : fiables, mais un lien manuel prime
        if th["spotify"] and t.spotify_state != MANUAL:
            t.spotify_id, t.spotify_state = th["spotify"], FOUND
        if th["youtube"] and t.youtube_state != MANUAL:
            t.youtube_id, t.youtube_state = th["youtube"], FOUND
        stored.append(t)

    # même thème des deux côtés : même type + même numéro (titre proche), sinon titre très proche
    used: set[int] = set()
    for t in stored:
        free = [x for x in asdb_songs if x["kind"] == t.kind and id(x) not in used]
        same_slot = [x for x in free if (x["seq"] or 1) == (t.seq or 1)
                     and match.title_sim(t.title, x["title"]) >= 0.5]
        by_title = [x for x in free if match.title_sim(t.title, x["title"]) >= 0.85]
        x = (same_slot or by_title or [None])[0]
        if x is not None:
            used.add(id(x))
            if x["rate"] is not None:
                t.amq_rate = x["rate"]
            if not t.artist:
                _set_artist(t, x["artist"])
    for x in asdb_songs:  # présents sur AnisongDB seulement (surtout des inserts)
        if id(x) in used or any(match.title_sim(t.title, x["title"]) >= 0.9 for t in stored):
            continue
        _store_asdb_track(s, x, a["id"], existing)


def _store_asdb_track(s, x: dict, at_anime_id: int, existing: dict):
    t = _upsert(s, f"asdb:{x['ann_song_id']}", existing)
    t.kind, t.at_anime_id = x["kind"], at_anime_id
    t.song_id = -x["song_id"] if x["song_id"] else None  # négatif : pas de collision avec AnimeThemes
    t.seq, t.title = x["seq"], x["title"]
    t.slug = f"{x['kind']}{x['seq']}" if x["seq"] else x["kind"]
    if x["length"] and not t.duration:
        t.duration = x["length"]
    if x["rate"] is not None:
        t.amq_rate = x["rate"]
    _set_artist(t, x["artist"])


def _store_asdb_anime(s, mal: int, songs: list[dict]):
    """Anime absent d'AnimeThemes : fiche locale d'id négatif (-id MAL)."""
    vintage = songs[0]["vintage"].split()
    row = s.get(AtAnime, -mal) or AtAnime(id=-mal)
    row.name, row.slug = songs[0]["anime"], ""
    row.season = vintage[0] if vintage else ""
    row.year = int(vintage[-1]) if vintage and vintage[-1].isdigit() else 0
    row.media_format, row.fetched_at = songs[0]["anime_type"], utcnow()
    row.mal_id = mal
    s.merge(row)
    keys = [f"asdb:{x['ann_song_id']}" for x in songs]
    existing = {t.key: t for t in s.query(Track).filter(Track.key.in_(keys))}
    for x in songs:
        _store_asdb_track(s, x, -mal, existing)


def _drop_stale_albums(s, root_id: int, keep_deezer_ids: set[int]):
    """Albums qui ne passent plus le filtre : on les retire (les pistes déjà mises en
    playlist restent, détachées de l'album)."""
    in_pl = {r.track_id for r in s.query(PlaylistItem.track_id)}
    for al in s.query(Album).filter(Album.root_id == root_id):
        if al.deezer_id in keep_deezer_ids:
            continue
        for t in s.query(Track).filter(Track.album_id == al.id):
            if t.id in in_pl:
                t.album_id = None
            else:
                s.delete(t)
        s.delete(al)
    s.flush()


def resolve_ost(root_id: int):
    """Albums de bande originale (Deezer) d'une série + leurs pistes."""
    s = db_session()
    try:
        f = s.get(Franchise, root_id)
        if f is None:
            return
        names = [f.title, f.english]
        for a in s.query(AtAnime).filter(AtAnime.id.in_(id_list(f.at_ids))).order_by(AtAnime.year):
            names.append(a.name)
        names = list(dict.fromkeys(n for n in names if n))
        albums = deezer.find_ost_albums(names, f.year)
        _drop_stale_albums(s, root_id, {a["id"] for a in albums})
        for full in albums:
            al = s.query(Album).filter_by(deezer_id=full["id"]).first() or Album(deezer_id=full["id"])
            al.root_id = root_id
            al.title, al.artist = full.get("title") or "", (full.get("artist") or {}).get("name") or ""
            al.cover = full.get("cover_medium") or full.get("cover") or ""
            al.nb_tracks, al.upc = full.get("nb_tracks") or 0, full.get("upc") or ""
            al.release_date = full.get("release_date") or ""
            s.add(al)
            s.flush()
            tracks = (full.get("tracks") or {}).get("data") or []
            if len(tracks) < (al.nb_tracks or 0):
                tracks = deezer.album_tracks(full["id"])
            keys = [f"dz:{t['id']}" for t in tracks]
            existing = {t.key: t for t in s.query(Track).filter(Track.key.in_(keys))} if keys else {}
            for i, dt in enumerate(tracks):
                t = _upsert(s, f"dz:{dt['id']}", existing)
                t.kind, t.album_id, t.seq = "OST", al.id, i + 1
                t.title = dt.get("title") or ""
                t.artist = (dt.get("artist") or {}).get("name") or al.artist
                t.duration, t.deezer_id = dt.get("duration") or 0, dt["id"]
                t.deezer_rank = dt.get("rank") or 0
                t.deezer_preview, t.deezer_state = dt.get("preview") or "", FOUND
                t.isrc = dt.get("isrc") or t.isrc or ""
        f.ost_state, f.ost_synced_at = "done", utcnow()
        s.commit()
        log.info("série %s « %s » : %d albums OST", root_id, f.title, len(albums))
    except Exception:
        s.rollback()
        f = s.get(Franchise, root_id)
        if f is not None:
            f.ost_state, f.ost_synced_at = "error", utcnow()
            s.commit()
        raise
    finally:
        s.close()
