"""Base SQLite du site musique (cache des titres + playlists des membres)."""
from datetime import datetime, timezone

from sqlalchemy import (Boolean, Column, DateTime, Float, Integer, String, Text,
                        UniqueConstraint, create_engine, event)
from sqlalchemy.orm import declarative_base, sessionmaker

from . import config

engine = create_engine(config.DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 30})


@event.listens_for(engine, "connect")
def _sqlite_pragmas(conn, _):
    # WAL : le site lit pendant que les workers de fond écrivent.
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc)


# États de recherche d'un lien (Spotify / YouTube / Deezer)
PENDING = ""        # pas encore cherché
FOUND = "found"     # trouvé automatiquement
NONE = "none"       # cherché, introuvable
MANUAL = "manual"   # collé à la main par un membre (jamais écrasé)


class Franchise(Base):
    """Une série de la liste anime (anime racine Kitsu = toutes ses saisons)."""
    __tablename__ = "franchises"
    root_id = Column(Integer, primary_key=True)          # id Kitsu de l'anime racine
    title = Column(String(400), default="")
    english = Column(String(400), default="")
    native = Column(String(400), default="")
    cover_url = Column(String(500), default="")
    group_key = Column(String(200), default="")
    year = Column(Integer, default=0)
    member_ids = Column(Text, default="")                # ids Kitsu de toutes les saisons, « ,1,2, »
    at_ids = Column(Text, default="")                    # anime AnimeThemes rattachés, « ,1,2, »
    at_state = Column(String(16), default="")            # "" à faire / done / error
    at_synced_at = Column(DateTime(timezone=True), nullable=True)
    ost_state = Column(String(16), default="")
    ost_synced_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=utcnow)


class AtAnime(Base):
    """Fiche AnimeThemes (une saison / un film)."""
    __tablename__ = "at_anime"
    id = Column(Integer, primary_key=True)               # id AnimeThemes
    slug = Column(String(200), default="")
    name = Column(String(400), default="")
    year = Column(Integer, default=0)
    season = Column(String(16), default="")
    media_format = Column(String(16), default="")
    mal_id = Column(Integer, nullable=True)
    fetched_at = Column(DateTime(timezone=True), default=utcnow)


class Album(Base):
    """Album de bande originale (OST) trouvé sur Deezer pour une franchise."""
    __tablename__ = "albums"
    id = Column(Integer, primary_key=True, autoincrement=True)
    root_id = Column(Integer, index=True, nullable=False)
    deezer_id = Column(Integer, unique=True, nullable=False)
    title = Column(String(400), default="")
    artist = Column(String(400), default="")
    cover = Column(String(500), default="")
    nb_tracks = Column(Integer, default=0)
    upc = Column(String(32), default="")
    release_date = Column(String(16), default="")
    spotify_id = Column(String(32), default="")
    spotify_state = Column(String(8), default=PENDING)


class Track(Base):
    """Un titre : OP / ED / insert (AnimeThemes) ou piste d'OST (Deezer)."""
    __tablename__ = "tracks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(64), unique=True, nullable=False)   # "at:<theme id>" | "dz:<track id>"
    kind = Column(String(4), nullable=False)                # OP / ED / IN / OST
    at_anime_id = Column(Integer, nullable=True, index=True)
    album_id = Column(Integer, nullable=True, index=True)
    song_id = Column(Integer, nullable=True, index=True)    # chanson AnimeThemes (partagée entre saisons/films)
    seq = Column(Integer, default=0)                        # n° d'OP/ED ou n° de piste
    slug = Column(String(16), default="")                   # "OP1", "ED2"…
    title = Column(String(400), default="")
    artist = Column(String(400), default="")
    episodes = Column(String(64), default="")
    at_audio = Column(String(300), default="")              # .ogg AnimeThemes (version TV)
    at_video = Column(String(300), default="")              # .webm AnimeThemes
    spoiler = Column(Boolean, default=False)
    nsfw = Column(Boolean, default=False)
    duration = Column(Integer, default=0)                   # secondes (OST)
    deezer_id = Column(Integer, nullable=True)
    deezer_preview = Column(String(300), default="")
    isrc = Column(String(16), default="")
    deezer_state = Column(String(8), default=PENDING)
    spotify_id = Column(String(32), default="")
    spotify_state = Column(String(8), default=PENDING)
    youtube_id = Column(String(16), default="")
    youtube_state = Column(String(8), default=PENDING)
    youtube_wanted = Column(Boolean, default=False)         # OST : recherche YouTube demandée
    # Popularité, pour la difficulté du blind test (None = pas encore mesuré)
    amq_rate = Column(Float, nullable=True)                 # % de bonnes réponses sur Anime Music Quiz
    at_plays = Column(Integer, nullable=True)               # playlists AnimeThemes contenant le thème
    deezer_rank = Column(Integer, nullable=True)            # popularité Deezer (0 = inconnu)
    yt_views = Column(Integer, nullable=True)               # vues de la vidéo YouTube retenue (-1 = inconnu)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Playlist(Base):
    __tablename__ = "playlists"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(32), index=True, nullable=False)
    name = Column(String(80), nullable=False, default="Ma playlist anime")
    position = Column(Integer, default=0)
    spotify_id = Column(String(32), default="")          # playlist Spotify créée depuis le site
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PlaylistItem(Base):
    __tablename__ = "playlist_items"
    __table_args__ = (UniqueConstraint("playlist_id", "track_id", name="uq_playlist_track"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    playlist_id = Column(Integer, index=True, nullable=False)
    track_id = Column(Integer, nullable=False)
    pos = Column(Float, default=0)
    added_at = Column(DateTime(timezone=True), default=utcnow)


class UserState(Base):
    __tablename__ = "user_state"
    user_id = Column(String(32), primary_key=True)
    name = Column(String(80), default="")
    avatar = Column(String(500), default="")
    active_playlist = Column(Integer, nullable=True)
    last_seen = Column(DateTime(timezone=True), default=utcnow)


class SpotifyAuth(Base):
    __tablename__ = "spotify_auth"
    user_id = Column(String(32), primary_key=True)
    access_token = Column(Text, default="")
    refresh_token = Column(Text, default="")
    expires_at = Column(Float, default=0)
    spotify_user = Column(String(64), default="")
    display_name = Column(String(120), default="")


def init_db():
    Base.metadata.create_all(engine)
    # mini-migration : colonnes ajoutées après coup (create_all ne touche pas aux tables existantes)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            have = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table.name})")}
            for col in table.columns:
                if col.name not in have:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}")


def db_session():
    return SessionLocal()


def id_list(s: str) -> list[int]:
    return [int(x) for x in (s or "").split(",") if x.strip().lstrip("-").isdigit()]


def id_str(ids) -> str:
    ids = sorted({int(i) for i in ids})
    return ("," + ",".join(map(str, ids)) + ",") if ids else ""
