"""Branche (ou change) l'app Spotify du site musique, sur le Pi :

    ssh -t pi@192.168.1.73 ~/anime-music/venv/bin/python ~/anime-music/set_spotify.py

Demande le Client ID et le Client Secret (masqué), les écrit dans .env (chmod 600)
puis redémarre le service. Le secret ne passe jamais ailleurs que dans ce terminal.
"""
import getpass
import os
import re
import subprocess

ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def ask(prompt: str, hidden: bool = False) -> str:
    while True:
        v = (getpass.getpass(prompt) if hidden else input(prompt)).strip()
        if re.fullmatch(r"[0-9a-f]{32}", v):
            return v
        print("  → 32 caractères hexadécimaux attendus (copie-le depuis developer.spotify.com).")


def main():
    base = "https://music.dwa.gold"
    lines = open(ENV, encoding="utf-8").read().splitlines() if os.path.exists(ENV) else []
    for ln in lines:
        if ln.startswith("BASE_URL="):
            base = ln.split("=", 1)[1].strip().rstrip("/")
    print("Dans ton app Spotify (developer.spotify.com/dashboard), la Redirect URI doit être :")
    print(f"  {base}/spotify/callback\n")
    cid = ask("Client ID     : ")
    secret = ask("Client Secret (masqué) : ", hidden=True)
    values = {"SPOTIFY_CLIENT_ID": cid, "SPOTIFY_CLIENT_SECRET": secret}
    out, done = [], set()
    for ln in lines:
        key = ln.split("=", 1)[0]
        if key in values:
            out.append(f"{key}={values[key]}")
            done.add(key)
        else:
            out.append(ln)
    out += [f"{k}={v}" for k, v in values.items() if k not in done]
    old = os.umask(0o077)
    try:
        with open(ENV, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
    finally:
        os.umask(old)
    os.chmod(ENV, 0o600)
    print("\n.env mis à jour, redémarrage du site…")
    subprocess.run(["sudo", "systemctl", "restart", "anime-music"], check=False)
    print("OK. Les liens Spotify vont se remplir en arrière-plan (quelques minutes),")
    print(f"puis « Créer sur Spotify » sur {base} te demandera ton compte une fois.")


if __name__ == "__main__":
    main()
