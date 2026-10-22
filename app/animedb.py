"""Lecture SEULE de la base du site anime (anime.dwa.gold) : membres + listes.

On ouvre le fichier SQLite en `mode=ro` : ce site ne peut rien y écrire.
"""
import sqlite3

from . import config

STATUS_LABELS = {"to_watch": "À regarder", "watching": "En cours", "finished": "Fini"}


def _conn():
    c = sqlite3.connect(f"file:{config.ANIME_DB}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def get_user(user_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute("select id, username, global_name, avatar, custom_avatar from users where id = ?",
                      (user_id,)).fetchone()
    if r is None:
        return None
    return {"id": r["id"], "name": r["global_name"] or r["username"] or "?", "avatar": _avatar(r)}


def _avatar(r) -> str:
    uid = str(r["id"])
    if r["custom_avatar"]:
        return f"{config.ANIME_SITE_URL}/uploads/{uid}.jpg?v={r['custom_avatar']}"
    if not uid.isdigit():
        return f"{config.ANIME_SITE_URL}/static/avatar-default.svg"
    if r["avatar"]:
        return f"https://cdn.discordapp.com/avatars/{uid}/{r['avatar']}.png?size=64"
    return f"https://cdn.discordapp.com/embed/avatars/{int(uid) % 5}.png"


def users_with_list() -> list[str]:
    with _conn() as c:
        return [r[0] for r in c.execute("select distinct user_id from entries")]


def user_list(user_id: str) -> list[dict]:
    """La liste d'un membre : une ligne par série (anime racine) + toutes ses saisons connues."""
    with _conn() as c:
        rows = c.execute(
            """select a.id, a.romaji, a.english, a.native, a.cover_url, a.group_key, a.season_year,
                      e.status, e.rating, e.updated_at
               from entries e join anime a on a.id = e.anime_id
               where e.user_id = ?""", (user_id,)).fetchall()
        out = []
        for r in rows:
            members = [m["id"] for m in c.execute(
                "select id from anime where id = ? or root_id = ? or (group_key = ? and group_key != '')",
                (r["id"], r["id"], r["group_key"] or "")).fetchall()]
            out.append({
                "root_id": r["id"],
                "title": r["romaji"] or r["english"] or r["native"] or f"#{r['id']}",
                "english": r["english"] or "",
                "native": r["native"] or "",
                "cover": r["cover_url"] or "",
                "group_key": r["group_key"] or "",
                "year": r["season_year"] or 0,
                "status": r["status"],
                "rating": r["rating"],
                "updated_at": str(r["updated_at"] or ""),
                "members": sorted(set(members) | {r["id"]}),
            })
    return out
