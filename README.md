# DWAnisongs

Les musiques des animes de ta liste → [music.dwa.gold](https://music.dwa.gold)

- Openings, endings, inserts et OST de chaque série de ta liste [DWAnime](https://anime.dwa.gold), saisons regroupées.
- Sources : AnimeThemes (audio/vidéo version TV), AnisongDB (artistes, inserts), Deezer (albums OST), YouTube (yt-dlp).
- Lecteur intégré, playlists, export vers Spotify ou YouTube.
- Workers de fond qui complètent le catalogue petit à petit.
- Connexion par SSO depuis le site anime (HMAC signé).

## Stack

Python · FastAPI · Uvicorn · SQLite · Jinja2 · yt-dlp

## Lancer en local

```bat
copy .env.example .env
python -m venv venv
venv\Scripts\pip install -r requirements.txt
run.bat
```

Puis http://127.0.0.1:8070. Spotify est optionnel (`set_spotify.py`). `mettre_a_jour_pi.bat` déploie sur le Raspberry Pi.
