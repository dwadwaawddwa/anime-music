"""Configuration chargée depuis l'environnement (.env)."""
import os
import secrets

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# URL publique de ce site (sert aux redirections OAuth Spotify)
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8070").rstrip("/")

# Secret de session : DOIT être fixe en prod, sinon les sessions sautent à chaque redémarrage.
SESSION_SECRET = os.getenv("SESSION_SECRET") or secrets.token_hex(32)
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1") == "1"

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'music.db')}")

# Base du site anime (lecture seule) : c'est elle qui donne la liste de chaque membre.
ANIME_DB = os.getenv("ANIME_DB", "/home/pi/anime-site/anime.db")
# Site anime : la connexion passe par lui (SSO signé HMAC avec SSO_SECRET, partagé).
ANIME_SITE_URL = os.getenv("ANIME_SITE_URL", "https://anime.dwa.gold").rstrip("/")
SSO_SECRET = os.getenv("SSO_SECRET", "")

# Mode LOCAL (dev) : ouvre directement la session de cet utilisateur, sans SSO.
LOCAL_USER_ID = os.getenv("LOCAL_USER_ID", "").strip()

# Spotify (optionnel) : app créée sur developer.spotify.com (compte Premium requis
# depuis 2026 pour le mode développement). Sans ça : liens AnimeThemes + recherche.
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REDIRECT_URI = BASE_URL + "/spotify/callback"
SPOTIFY_MARKET = os.getenv("SPOTIFY_MARKET", "CA")

# Recherche YouTube via yt-dlp (pas de clé API). 0 pour couper.
YOUTUBE_SEARCH = os.getenv("YOUTUBE_SEARCH", "1") == "1"

# Les workers de fond (résolution des liens) tournent dans le process web. 0 pour les tests.
WORKERS = os.getenv("WORKERS", "1") == "1"

# Fraîcheur du rapprochement anime ↔ AnimeThemes (nouvelles saisons, nouveaux OP…)
FRANCHISE_TTL_DAYS = int(os.getenv("FRANCHISE_TTL_DAYS", "7"))

USER_AGENT = "dwa-music/1.0 (+https://music.dwa.gold)"


def spotify_configured() -> bool:
    return bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)
