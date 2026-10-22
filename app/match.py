"""Comparaison floue titre/artiste entre AnimeThemes (romaji) et Deezer/Spotify/YouTube.

Les plateformes écrivent souvent le titre en japonais (紅蓮の弓矢) là où AnimeThemes
donne le romaji (Guren no Yumiya) → on romanise le japonais avec pykakasi avant de comparer.
"""
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache

try:
    from pykakasi import kakasi
    _KKS = kakasi()
except Exception:  # pragma: no cover - pykakasi absent : on compare sans romaniser
    _KKS = None

_CJK = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")
_PARENS = re.compile(r"[(\[（【「『〈《<~～][^)\]）】」』〉》>~～]*[)\]）】」』〉》>~～]?")

# Versions qu'on ne veut pas à la place de l'original (sauf si le titre AnimeThemes le dit)
BAD_WORDS = [
    "tv size", "tv ver", "tv version", "tv edit", "instrumental", "off vocal", "karaoke", "cover",
    "remix", "acoustic", "piano", "8bit", "8 bit", "music box", "orgel", "orchestra", "nightcore",
    "slowed", "sped up", "reverb", "8d", "lofi", "lo fi", "mashup", "english ver", "english version",
    "version francaise", "french version", "amv", "reaction", "1 hour", "loop", "guitar", "violin",
    "live", "remaster", "short ver", "inst", "bass boosted", "kalimba", "ai cover", "fan made",
]


@lru_cache(maxsize=20000)
def romanize(s: str) -> str:
    if not s or not _KKS or not _CJK.search(s):
        return s or ""
    return " ".join(part["hepburn"] for part in _KKS.convert(s))


@lru_cache(maxsize=40000)
def norm(s: str) -> str:
    s = romanize(s or "")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    # romaji : ou/oo/ō → o, uu → u (les plateformes écrivent « Jiyu » ou « Jiyuu »)
    # (appliqué des deux côtés, donc sans risque pour l'anglais : « you » → « yo » partout)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = s.replace("ou", "o").replace("oo", "o").replace("uu", "u")
    return re.sub(r"\s+", " ", s).strip()


def variants(s: str) -> set[str]:
    """Titre complet, titre sans parenthèses, et contenu des parenthèses."""
    out = {norm(s), norm(_PARENS.sub(" ", s or ""))}
    for m in re.findall(r"[(\[（【「『]([^)\]）】」』]+)[)\]）】」』]", s or ""):
        out.add(norm(m))
    return {v for v in out if v}


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    r = SequenceMatcher(None, a, b).ratio()
    short, long_ = sorted((a, b), key=len)
    if len(short) >= 4 and re.search(rf"\b{re.escape(short)}\b", long_):
        r = max(r, 0.9)
    return r


def title_sim(a: str, b: str) -> float:
    return max((_ratio(x, y) for x in variants(a) for y in variants(b)), default=0.0)


def split_artists(s: str) -> list[str]:
    parts = re.split(r",|&|\bfeat\.?|\bft\.?|\bx\b|\bwith\b|/|、", s or "", flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def artist_sim(expected: str, found: str) -> float:
    """Meilleure correspondance d'un des artistes attendus dans le(s) artiste(s) trouvé(s)."""
    exp = [norm(a) for a in split_artists(expected)] or [norm(expected)]
    got = norm(found)
    if not got or not any(exp):
        return 0.0
    best = 0.0
    for e in exp:
        if not e:
            continue
        if re.search(rf"\b{re.escape(e)}\b", got):
            return 1.0
        best = max(best, _ratio(e, got), *(_ratio(e, norm(g)) for g in split_artists(found)))
    return best


def penalty(candidate: str, reference: str) -> float:
    c, r = f" {norm(candidate)} ", f" {norm(reference)} "
    p = 0.0
    for w in BAD_WORDS:
        if f" {w} " in c and f" {w} " not in r:
            p += 0.12 if w.startswith("tv") else 0.25
    return p


def contains_tokens(needle: str, hay: str) -> float:
    """Part des mots du titre attendu présents dans un titre YouTube (bruité)."""
    best = 0.0
    nh = norm(hay)
    h, glued = set(nh.split()), nh.replace(" ", "")
    for v in variants(needle):
        toks = v.split()
        if not toks:
            continue
        # « Zankoku na » vs « Zankokuna » : même titre, découpage différent
        if len(v) >= 4 and v.replace(" ", "") in glued:
            return 1.0
        best = max(best, sum(t in h for t in toks) / len(toks))
    return best
