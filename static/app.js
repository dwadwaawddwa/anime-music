/* Anisongs · dwa — bibliothèque des musiques de la liste anime, lecteur et playlists.
   Pas de framework : rendu par chaînes HTML échappées + délégation d'événements
   (CSP stricte : aucun handler inline). */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const CSRF = $('meta[name="csrf"]').content;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fold = (s) => String(s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
const fmtTime = (s) => { s = Math.max(0, Math.floor(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const ICON_PLAY = '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';
const ICON_PAUSE = '<svg viewBox="0 0 24 24"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>';
const use = (id) => `<svg viewBox="0 0 24 24"><use href="#${id}"/></svg>`;
const KIND_LABEL = { OP: "Opening", ED: "Ending", IN: "Insert", OST: "OST" };

function store(key, val) {
  try {
    if (val === undefined) return JSON.parse(localStorage.getItem("anisongs:" + key));
    localStorage.setItem("anisongs:" + key, JSON.stringify(val));
  } catch { return null; }
}

/* ------------------------------------------------------------------ état */
const S = {
  lib: [],
  tracks: new Map(),
  playlists: [],
  active: null,
  spotify: {},
  progress: {},
  openAlbums: new Set(),
  closed: new Set(store("closed") || []),
  f: Object.assign({ kinds: ["OP", "ED", "IN"], avail: "all", status: "all", sort: "title", notin: false, q: "" },
    store("filters") || {}, { q: "" }),
};
const activePl = () => S.playlists.find((p) => p.id === S.active) || S.playlists[0];
const activeSet = () => new Set(activePl()?.items || []);

/* ------------------------------------------------------------------ API */
async function api(path, data, method) {
  const opts = { method: method || (data !== undefined ? "POST" : "GET"), headers: { "X-CSRF": CSRF } };
  if (data !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(data);
  }
  const r = await fetch(path, opts);
  let j = {};
  try { j = await r.json(); } catch { /* corps vide */ }
  if (r.status === 401) { location.href = "/"; throw new Error("Session expirée"); }
  if (!r.ok) throw new Error(j.error || `Erreur ${r.status}`);
  return j;
}

let toastTimer;
function toast(msg, err = false, ms = 3800) {
  const el = $("#toast");
  el.innerHTML = msg;
  el.classList.toggle("err", err);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), ms);
}

/* ------------------------------------------------------------------ chargement */
function indexLibrary(data) {
  S.lib = data.anime;
  S.tracks = new Map();
  for (const a of S.lib) {
    const seen = new Set();
    a.total = 0;
    for (const se of a.seasons) {
      for (const t of se.tracks) {
        t.anime = a; t.season = se;
        t.dupe = !!(t.sg && seen.has(t.sg)); // même chanson dans un film récap / une autre saison
        if (t.sg) seen.add(t.sg);
        S.tracks.set(t.id, t);
        if (!t.dupe) a.total++;
      }
    }
    for (const al of a.albums) {
      for (const t of al.tracks) { t.anime = a; t.album = al; S.tracks.set(t.id, t); }
    }
  }
  for (const [id, t] of Object.entries(data.extra || {})) {
    if (!S.tracks.has(+id)) { t.anime = null; t.orphan = true; S.tracks.set(+id, t); }
  }
}

async function load(silent = false) {
  try {
    const data = await api("/api/library");
    indexLibrary(data);
    S.playlists = data.playlists;
    S.active = data.active;
    S.spotify = data.spotify;
    S.progress = data.progress;
    renderAll();
    if (P.cur) { const fresh = S.tracks.get(P.cur.id); if (fresh) P.cur = fresh; }
  } catch (e) {
    if (!silent) $("#list").innerHTML = `<div class="empty">Impossible de charger : ${esc(e.message)}</div>`;
  }
}

/* ------------------------------------------------------------------ filtres */
function trackPasses(t, inPl) {
  const f = S.f;
  if (t.dupe || !f.kinds.includes(t.k)) return false;
  if (f.avail === "spotify" && !t.sp) return false;
  if (f.avail === "ytonly" && (t.sp || !t.yt)) return false;
  if (f.avail === "missing" && (t.sp || t.yt)) return false;
  if (f.notin && inPl.has(t.id)) return false;
  return true;
}

function animeView(a, inPl) {
  const q = fold(S.f.q);
  const animeHit = !q || fold(a.title + " " + a.english).includes(q);
  const ok = (t) => trackPasses(t, inPl) && (animeHit || fold(t.t + " " + (t.a || "")).includes(q)
    || (t.album && fold(t.album.title).includes(q)));
  const seasons = a.seasons.map((se) => ({ se, tracks: se.tracks.filter(ok) })).filter((x) => x.tracks.length);
  const albums = S.f.kinds.includes("OST")
    ? a.albums.map((al) => ({ al, tracks: al.tracks.filter(ok) })).filter((x) => x.tracks.length) : [];
  const n = seasons.reduce((s, x) => s + x.tracks.length, 0) + albums.reduce((s, x) => s + x.tracks.length, 0);
  return { a, seasons, albums, n, animeHit };
}

function sortedViews() {
  const inPl = activeSet();
  let list = S.lib.filter((a) => S.f.status === "all" || a.status === S.f.status).map((a) => animeView(a, inPl));
  const filtering = S.f.q || S.f.avail !== "all" || S.f.notin;
  list = list.filter((v) => v.n || (!filtering && v.animeHit));
  const bare = (s) => s.replace(/^[^\p{L}\p{N}]+/u, ""); // « [Oshi no Ko] » rangé à O
  const by = {
    title: (x, y) => bare(x.a.title).localeCompare(bare(y.a.title), "fr", { sensitivity: "base", numeric: true }),
    rating: (x, y) => (y.a.rating ?? -1) - (x.a.rating ?? -1) || x.a.title.localeCompare(y.a.title),
    recent: (x, y) => String(y.a.updated).localeCompare(String(x.a.updated)),
    count: (x, y) => y.n - x.n,
  }[S.f.sort] || (() => 0);
  return list.sort(by);
}

function flatTracks(views) {
  const out = [];
  for (const v of views) {
    for (const x of v.seasons) out.push(...x.tracks);
    for (const x of v.albums) out.push(...x.tracks);
  }
  return out;
}

/* ------------------------------------------------------------------ rendu liste */
function spLink(t) {
  if (t.sp) return `<a class="lk sp on" href="https://open.spotify.com/track/${esc(t.sp)}" target="_blank" rel="noopener" title="Ouvrir dans Spotify">${use("i-spotify")}</a>`;
  const search = `https://open.spotify.com/search/${encodeURIComponent(`${t.t} ${t.a || ""}`)}`;
  if (t.ss === "" && S.spotify.configured) return `<span class="lk sp wait" title="Recherche Spotify en cours…">${use("i-spotify")}</span>`;
  const why = S.spotify.configured ? "Pas trouvé automatiquement — chercher sur Spotify" : "Chercher sur Spotify";
  return `<a class="lk sp none" href="${search}" target="_blank" rel="noopener" title="${why}">${use("i-spotify")}</a>`;
}

function ytLink(t) {
  if (t.yt) return `<a class="lk yt on" href="https://www.youtube.com/watch?v=${esc(t.yt)}" target="_blank" rel="noopener" title="Ouvrir sur YouTube">${use("i-yt")}</a>`;
  if (t.ys === "" && t.k !== "OST" && S.progress.youtube_pending) return `<span class="lk yt wait" title="Recherche YouTube en cours…">${use("i-yt")}</span>`;
  const q = encodeURIComponent(`${t.a || ""} ${t.t}${t.k === "OST" && t.anime ? " " + t.anime.title : ""}`);
  return `<a class="lk yt none" href="https://www.youtube.com/results?search_query=${q}" target="_blank" rel="noopener" title="Chercher sur YouTube">${use("i-yt")}</a>`;
}

function tagLabel(t) {
  if (t.k === "OST") return `#${t.album ? t.album.tracks.indexOf(t) + 1 : ""}`;
  return t.s || t.k;
}

function artLine(t) {
  const bits = [t.a || "Artiste inconnu"];
  if (t.ep) bits.push(`ép. ${t.ep}`);
  if (t.k === "OST" && t.d) bits.push(fmtTime(t.d));
  return bits.join(" · ");
}

function rowHTML(t, inPl) {
  const on = inPl.has(t.id);
  const playing = P.cur && P.cur.id === t.id;
  return `<div class="trk${playing ? " playing" : ""}" data-id="${t.id}">
    <button class="play" data-act="play" aria-label="Écouter ${esc(t.t)}">${playing && P.playing ? ICON_PAUSE : ICON_PLAY}</button>
    <span class="tag ${t.k}" title="${KIND_LABEL[t.k]}">${esc(tagLabel(t))}</span>
    <div class="meta"><div class="ttl" title="${esc(t.t)}">${esc(t.t)}</div><div class="art">${esc(artLine(t))}</div></div>
    <div class="links">${spLink(t)}${ytLink(t)}${t.v ? `<button class="lk vid" data-act="video" title="Voir l'${t.k === "ED" ? "ending" : "opening"} en vidéo">${use("i-film")}</button>` : ""}<button class="lk edit" data-act="edit" title="Corriger les liens">${use("i-edit")}</button></div>
    <button class="add${on ? " in" : ""}" data-act="add" title="${on ? "Retirer de" : "Ajouter à"} la playlist">${on ? "✓" : "+"}</button>
  </div>`;
}

function countsHTML(v) {
  const c = { OP: 0, ED: 0, IN: 0 };
  let sp = 0;
  for (const x of v.seasons) for (const t of x.tracks) { c[t.k]++; if (t.sp) sp++; }
  const parts = [];
  if (c.OP) parts.push(`<span class="count">${c.OP} OP</span>`);
  if (c.ED) parts.push(`<span class="count">${c.ED} ED</span>`);
  if (c.IN) parts.push(`<span class="count">${c.IN} insert${c.IN > 1 ? "s" : ""}</span>`);
  if (v.a.albums.length) parts.push(`<span class="count">${v.a.albums.length} album${v.a.albums.length > 1 ? "s" : ""} OST</span>`);
  if (sp) parts.push(`<span class="count sp">${sp} sur Spotify</span>`);
  return parts.join("");
}

function animeHTML(v, inPl) {
  const a = v.a;
  const closed = S.closed.has(a.id);
  const pending = a.at_state === "" || (a.ost_state === "" && S.f.kinds.includes("OST"));
  let body = "";
  for (const x of v.seasons) {
    const meta = [x.se.year, x.se.format].filter(Boolean).join(" · ");
    if (a.seasons.length > 1) body += `<div class="season-h">${esc(x.se.name)} <small>${esc(meta)}</small></div>`;
    body += x.tracks.map((t) => rowHTML(t, inPl)).join("");
  }
  if (v.albums.length) {
    body += `<div class="season-h">Bandes originales <small>${v.albums.length} album${v.albums.length > 1 ? "s" : ""}</small></div>`;
    for (const x of v.albums) {
      const open = S.openAlbums.has(x.al.id);
      body += `<details class="album" data-album="${x.al.id}"${open ? " open" : ""}>
        <summary><img src="${esc(x.al.cover)}" alt="" loading="lazy">
          <div class="meta"><div class="al-t" title="${esc(x.al.title)}">${esc(x.al.title)}</div>
            <div class="al-s">${esc(x.al.artist)} · ${esc(x.al.year)} · ${x.tracks.length} piste${x.tracks.length > 1 ? "s" : ""}${x.al.sp ? ' · <span class="sp-ok">Spotify ✓</span>' : ""}</div></div>
          <div class="al-actions"><button class="lk" data-act="play-album" title="Écouter l'album">${ICON_PLAY}</button>
            <button class="btn" data-act="add-album" title="Ajouter tout l'album">＋ Album</button></div>
        </summary>
        <div class="al-body">${open ? x.tracks.map((t) => rowHTML(t, inPl)).join("") : ""}</div>
      </details>`;
    }
  }
  if (!v.n) {
    body = a.at_state === "" ? `<div class="pending-note">Recherche des openings / endings en cours…</div>`
      : `<div class="pending-note">Aucune musique trouvée pour cet anime sur AnimeThemes${a.ost_state === "done" ? " ni d'OST sur Deezer" : ""}.</div>`;
  } else if (pending) {
    body += `<div class="pending-note">${a.at_state === "" ? "Recherche des openings / endings…" : "Recherche des albums OST…"}</div>`;
  }
  const rating = a.rating != null ? `<span class="rating">★ ${a.rating}</span>` : "";
  return `<section class="anime${closed ? " closed" : ""}" id="a-${a.id}" data-anime="${a.id}" data-cover="${esc(a.cover)}">
    <div class="a-head">
      <img class="cover" src="${esc(a.cover)}" alt="" loading="lazy">
      <div class="a-info">
        <h2>${esc(a.title)}</h2>
        <div class="a-sub">${a.english && a.english !== a.title ? `<span>${esc(a.english)}</span>` : ""}
          <span class="st ${esc(a.status)}">${esc(a.status_label)}</span>${rating}</div>
        <div class="counts">${countsHTML(v)}</div>
      </div>
      <div class="a-actions">
        <button class="btn" data-act="play-anime" ${v.n ? "" : "disabled"}>▶ Écouter</button>
        <button class="btn" data-act="add-anime" ${v.n ? "" : "disabled"} title="Ajouter tous les titres affichés de cet anime">＋ Tout (${v.n})</button>
        <button class="icon-btn collapse-btn" data-act="collapse" aria-label="Replier"><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></button>
      </div>
    </div>
    <div class="a-body">${body}</div>
  </section>`;
}

let views = [];
function renderList() {
  const inPl = activeSet();
  views = sortedViews();
  const list = $("#list");
  const p = S.progress;
  if (!S.lib.length) {
    list.innerHTML = `<div class="empty">Ta liste anime est vide. Ajoute des animes sur
      <a href="${esc($('meta[name="anime-url"]').content)}/list">anime.dwa.gold</a> !</div>`;
  } else if (!views.length) {
    const firstRun = p.series && p.at_pending >= p.series * 0.9;
    list.innerHTML = firstRun
      ? `<div class="empty"><b>On récupère les musiques de tes ${p.series} animes…</b><br>
         La première fois ça prend quelques minutes (openings, endings, puis les liens Spotify / YouTube).
         <div class="loading-bar"><i></i></div></div>`
      : `<div class="empty">Aucun titre ne correspond à ces filtres.</div>`;
    const bar = $(".loading-bar i", list);
    if (bar) bar.style.width = `${Math.round(100 * (p.series - p.at_pending) / p.series)}%`;
  } else {
    list.innerHTML = views.map((v) => animeHTML(v, inPl)).join("");
    // CSP sans 'unsafe-inline' : pas d'attribut style dans le HTML, on passe par le CSSOM
    $$(".anime[data-cover]", list).forEach((el) => {
      if (el.dataset.cover) el.style.setProperty("--cover", `url("${el.dataset.cover.replace(/["\\\n]/g, "")}")`);
    });
  }
  const flat = flatTracks(views).filter((t) => !inPl.has(t.id));
  $("#visible-count").textContent = flat.length;
  $("#add-visible").disabled = !flat.length;
  renderRail();
}

function renderRail() {
  const rail = $("#rail");
  rail.innerHTML = `<div class="rail-title">${views.length} anime${views.length > 1 ? "s" : ""}</div>` + views.map((v) =>
    `<a href="#a-${v.a.id}" data-rail="${v.a.id}"><img src="${esc(v.a.cover)}" alt="" loading="lazy"><span>${esc(v.a.title)}</span><small>${v.n}</small></a>`).join("");
  observeSections();
}

let observer;
function observeSections() {
  observer?.disconnect();
  observer = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      $$("#rail a.cur").forEach((x) => x.classList.remove("cur"));
      const link = $(`#rail a[data-rail="${e.target.dataset.anime}"]`);
      if (link) { link.classList.add("cur"); link.scrollIntoView({ block: "nearest" }); }
    }
  }, { rootMargin: "-30% 0px -60% 0px" });
  $$(".anime").forEach((s) => observer.observe(s));
}

/* ------------------------------------------------------------------ panneau playlist */
function renderPanel() {
  const pl = activePl();
  const sel = $("#pl-select");
  sel.innerHTML = S.playlists.map((p) => `<option value="${p.id}"${p.id === S.active ? " selected" : ""}>${esc(p.name)} (${p.items.length})</option>`).join("");
  $("#pl-delete").disabled = S.playlists.length <= 1;
  const items = (pl?.items || []).map((id) => S.tracks.get(id)).filter(Boolean);
  const sp = items.filter((t) => t.sp).length;
  const yt = items.filter((t) => t.yt).length;
  $("#pl-badge").textContent = items.length;
  $("#pl-stats").innerHTML = `<span><b>${items.length}</b> titre${items.length > 1 ? "s" : ""}</span>
    <span class="sp">${sp} sur Spotify</span><span class="yt">${yt} sur YouTube</span>`;
  $("#pl-play").disabled = $("#pl-shuffle").disabled = !items.length;
  $("#pl-copy").disabled = !sp;
  $("#pl-yt").disabled = !yt;
  $("#pl-txt").href = pl ? `/api/playlists/${pl.id}/export.txt` : "#";
  $("#pl-spotify span").textContent = pl?.spotify_id ? "Mettre à jour sur Spotify" : "Créer sur Spotify";
  const acc = $("#sp-account");
  if (!S.spotify.configured) acc.innerHTML = "Création auto pas encore branchée : utilise « Copier les liens » puis Ctrl+V dans une playlist Spotify.";
  else if (S.spotify.connected) acc.innerHTML = `Compte Spotify : <b>${esc(S.spotify.name)}</b> · <button data-act="sp-logout">déconnecter</button>`;
  else acc.innerHTML = "Ton compte Spotify sera demandé à la première création.";
  if (pl?.spotify_id) acc.innerHTML += ` · <a href="https://open.spotify.com/playlist/${esc(pl.spotify_id)}" target="_blank" rel="noopener">ouvrir</a>`;

  const ol = $("#pl-items");
  if (!items.length) {
    ol.innerHTML = `<li class="pl-empty">Clique sur <b>+</b> à côté d'un titre, ou « Tout ajouter » en haut, pour remplir cette playlist.</li>`;
    return;
  }
  ol.innerHTML = items.map((t) => `<li draggable="true" data-id="${t.id}" class="${P.cur && P.cur.id === t.id ? "playing" : ""}">
      <span class="tag ${t.k}">${esc(t.k === "OST" ? "OST" : t.s || t.k)}</span>
      <div class="meta" data-act="play-pl"><div class="ttl">${esc(t.t)}</div><div class="art">${esc(t.anime ? t.anime.title : t.a || "")}</div></div>
      <button class="rm" data-act="rm" aria-label="Retirer">✕</button></li>`).join("");
}

function renderAll() {
  renderList();
  renderPanel();
  renderSync();
}

/* mise à jour légère après un ajout/retrait (sans tout re-dessiner) */
function refreshAddButtons() {
  const inPl = activeSet();
  $$(".trk").forEach((row) => {
    const on = inPl.has(+row.dataset.id);
    const b = $(".add", row);
    b.classList.toggle("in", on);
    b.textContent = on ? "✓" : "+";
    b.title = `${on ? "Retirer de" : "Ajouter à"} la playlist`;
  });
  const flat = flatTracks(views).filter((t) => !inPl.has(t.id));
  $("#visible-count").textContent = flat.length;
  $("#add-visible").disabled = !flat.length;
  renderPanel();
}

/* ------------------------------------------------------------------ actions playlist */
async function addTracks(tracks, label) {
  const pl = activePl();
  const have = new Set(pl.items);
  const seenSongs = new Set();
  const ids = [];
  for (const t of tracks) {
    if (have.has(t.id)) continue;
    if (t.sg) { if (seenSongs.has(t.sg)) continue; seenSongs.add(t.sg); }
    ids.push(t.id);
  }
  if (!ids.length) { toast("Déjà tout dans la playlist ✓"); return; }
  try {
    const r = await api(`/api/playlists/${pl.id}/add`, { ids });
    pl.items = r.items;
    refreshAddButtons();
    toast(`${r.added} titre${r.added > 1 ? "s" : ""} ajouté${r.added > 1 ? "s" : ""}${label ? " — " + esc(label) : ""} → <b>${esc(pl.name)}</b>`);
  } catch (e) { toast(esc(e.message), true); }
}

async function removeTracks(ids) {
  const pl = activePl();
  try {
    const r = await api(`/api/playlists/${pl.id}/remove`, { ids });
    pl.items = r.items;
    refreshAddButtons();
  } catch (e) { toast(esc(e.message), true); }
}

async function toggleTrack(t) {
  if (activeSet().has(t.id)) await removeTracks([t.id]);
  else await addTracks([t]);
}

/* ------------------------------------------------------------------ lecteur
   Chaque titre a une liste de sources essayées dans l'ordre (selon le mode). Si une
   source ne démarre pas en quelques secondes — le serveur audio d'AnimeThemes met
   parfois 10-20 s à répondre — on passe toute seule à la suivante (YouTube en général).
   La musique continue vidéo masquée : le panneau vidéo n'est qu'un affichage. */
const SILENCE = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";
const STALL_MS = { audio: 6000, video: 8000, yt: 10000 };
const P = {
  queue: [], idx: -1, cur: null, playing: false, started: false, engine: null, curSrc: "",
  cands: [], ci: 0, token: null, stallTimer: null,
  mode: store("mode") || "full", shuffle: false, repeat: false,
  audio: $("#a-el"), video: $("#v-el"), yt: null, ytReady: null, ytTimer: null,
  vol: store("vol") ?? 80, showVideo: false,
};
P.showVideo = P.mode === "video";

function loadYT() {
  if (P.ytReady) return P.ytReady;
  P.ytReady = new Promise((resolve, reject) => {
    window.onYouTubeIframeAPIReady = () => {
      P.yt = new YT.Player("yt-el", {
        width: "100%", height: "100%",
        playerVars: { autoplay: 1, playsinline: 1, rel: 0, modestbranding: 1 },
        events: {
          onReady: () => { P.yt.setVolume(P.vol); resolve(P.yt); },
          onStateChange: (e) => {
            if (P.engine !== "yt") return;
            if (e.data === YT.PlayerState.ENDED) next(true);
            if (e.data === YT.PlayerState.PLAYING) { P.started = true; setLoading(false); setPlaying(true); }
            if (e.data === YT.PlayerState.PAUSED) setPlaying(false);
          },
          onError: () => { if (P.engine === "yt") tryNextSource("YouTube refuse cette vidéo"); },
        },
      });
    };
    const s = document.createElement("script");
    s.src = "https://www.youtube.com/iframe_api";
    s.onerror = () => { P.ytReady = null; reject(new Error("YouTube injoignable")); };
    document.head.appendChild(s);
  });
  return P.ytReady;
}

async function ensureYtId(t) {
  if (t.yt || t.ys !== "") return t.yt;
  try {
    const r = await api(`/api/tracks/${t.id}/youtube`, {});
    t.yt = r.yt; t.ys = r.ys;
  } catch { /* pas grave : autre source */ }
  return t.yt;
}

/* Ordre des sources selon le mode. YouTube « paresseux » : l'id n'est cherché qu'au
   moment où on en a besoin. */
function sourcesFor(t, video = false) {
  const yt = t.yt ? { e: "yt", src: t.yt } : (t.ys === "" ? { e: "yt", lazy: true } : null);
  const au = t.au ? { e: "audio", src: t.au } : null;
  const v = t.v ? { e: "video", src: t.v } : null;
  const pv = t.pv ? { e: "audio", src: t.pv, preview: true } : null;
  const order = video || P.mode === "video" ? [v, yt, au, pv]
    : P.mode === "full" ? [yt, au, pv, v] : [au, yt, pv, v];
  return order.filter(Boolean);
}

function stopEngines() {
  clearTimeout(P.stallTimer);
  clearInterval(P.ytTimer);
  P.curSrc = "";
  P.audio.pause(); P.audio.removeAttribute("src"); P.audio.load();
  P.video.pause(); P.video.removeAttribute("src"); P.video.load();
  if (P.yt && P.yt.stopVideo) P.yt.stopVideo();
}

function setPlaying(on) {
  P.playing = on;
  $("#p-play-icon").setAttribute("d", on ? "M7 5h4v14H7zM13 5h4v14h-4z" : "M8 5v14l11-7z");
  $$(".trk.playing .play").forEach((b) => { b.innerHTML = on ? ICON_PAUSE : ICON_PLAY; });
  if ("mediaSession" in navigator) navigator.mediaSession.playbackState = on ? "playing" : "paused";
}

function setLoading(on) {
  $("#player").classList.toggle("loading", on);
  $$(".trk.playing").forEach((r) => r.classList.toggle("loading", on));
}

function markPlaying() {
  $$(".trk.playing").forEach((r) => { r.classList.remove("playing", "loading"); $(".play", r).innerHTML = ICON_PLAY; });
  $$("#pl-items li.playing").forEach((r) => r.classList.remove("playing"));
  if (!P.cur) return;
  $$(`.trk[data-id="${P.cur.id}"]`).forEach((r) => r.classList.add("playing"));
  $$(`#pl-items li[data-id="${P.cur.id}"]`).forEach((r) => r.classList.add("playing"));
}

/* Panneau vidéo : visible en mode Vidéo, ou si on l'a ouvert avec le bouton ▣.
   Masqué, il est juste poussé hors de l'écran : le son continue. */
function renderDock() {
  const kind = P.engine === "video" || P.engine === "yt" ? P.engine : null;
  const show = !!kind && P.showVideo;
  $("#dock").classList.toggle("away", !show);
  $("#p-vid").hidden = !kind;
  $("#p-vid").classList.toggle("on", show);
  P.video.hidden = kind !== "video";
  $("#yt-wrap").hidden = kind !== "yt";
  if (P.cur) $("#dock-title").textContent = `${P.cur.anime ? P.cur.anime.title + " — " : ""}${P.cur.s || P.cur.k} ${P.cur.t}`;
}

function playTrack(t, opts = {}) {
  stopEngines();
  P.cur = t;
  P.token = Symbol();
  if (opts.video) P.showVideo = true;
  markPlaying();
  const cover = t.album ? t.album.cover : t.anime?.cover || "";
  $("#np-cover").src = cover || "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
  $("#np-title").textContent = t.t;
  $("#np-sub").textContent = [t.a, t.anime ? `${t.anime.title} · ${t.k === "OST" ? "OST" : t.s}` : ""].filter(Boolean).join(" — ");
  document.title = `${t.t} · DWAnisongs`;
  if ("mediaSession" in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: t.t, artist: t.a || "", album: t.anime ? t.anime.title : "", artwork: cover ? [{ src: cover }] : [],
    });
  }
  P.cands = sourcesFor(t, opts.video);
  P.ci = -1;
  tryNextSource(); // synchrone jusqu'au play() : le clic compte comme geste utilisateur
}

function tryNextSource(reason) {
  P.ci++;
  const c = P.cands[P.ci];
  stopEngines();
  P.started = false;
  if (!c) {
    setLoading(false);
    toast(`Impossible de lire « ${esc(P.cur?.t)} »${reason ? " — " + esc(reason) : ""}`, true);
    setPlaying(false);
    P.engine = null;
    renderDock();
    if (P.queue.length > 1) setTimeout(() => next(true), 1200);
    return;
  }
  setLoading(true);
  if (c.lazy) { // id YouTube pas encore connu : on le cherche maintenant
    const token = P.token, t = P.cur;
    P.engine = null;
    ensureYtId(t).then((id) => {
      if (token !== P.token) return; // un autre titre a été lancé entre-temps
      if (id) { c.lazy = false; c.src = id; P.ci--; }
      tryNextSource(id ? undefined : "pas de vidéo YouTube");
    });
    return;
  }
  startSource(c);
}

function startSource(c) {
  P.engine = c.e;
  P.curSrc = c.src;
  const token = P.token;
  clearTimeout(P.stallTimer);
  P.stallTimer = setTimeout(() => {
    if (token === P.token && !P.started && P.curSrc === c.src) {
      toast(c.e === "yt" ? "YouTube ne répond pas, j'essaie une autre source…"
        : "Source trop lente, je passe à la suivante…");
      tryNextSource();
    }
  }, STALL_MS[c.e]);
  if (c.e === "audio" || c.e === "video") {
    const el = c.e === "audio" ? P.audio : P.video;
    el.src = c.src;
    el.volume = P.vol / 100;
    el.play().catch((e) => {
      if (P.curSrc !== c.src) return; // source déjà remplacée (AbortError)
      if (e.name === "NotAllowedError") { // navigateur qui exige un clic (iPhone…)
        clearTimeout(P.stallTimer); setLoading(false); setPlaying(false);
        toast("Appuie sur ▶ pour lancer la lecture");
      } else if (e.name === "NotSupportedError") tryNextSource("format non supporté");
    });
    if (c.preview) toast("Extrait de 30 s (Deezer) — version complète introuvable");
  } else {
    loadYT().then((yt) => {
      if (P.curSrc !== c.src || P.engine !== "yt") return;
      yt.setVolume(P.vol);
      yt.loadVideoById(c.src);
      clearInterval(P.ytTimer);
      P.ytTimer = setInterval(() => updateSeek(yt.getCurrentTime?.() || 0, yt.getDuration?.() || 0), 500);
    }).catch((e) => tryNextSource(e.message));
  }
  renderDock();
}

/* iPhone / Safari : un élément audio n'a le droit de jouer que s'il a été lancé une
   première fois pendant un clic. On le « débloque » au tout premier clic de la page. */
document.addEventListener("click", () => {
  if (P.audio.getAttribute("src")) return;
  P.audio.src = SILENCE;
  P.audio.play().catch(() => {}).finally(() => {
    if (P.audio.getAttribute("src") === SILENCE) P.audio.removeAttribute("src");
  });
}, { once: true, capture: true });

function playQueue(tracks, start = 0, opts = {}) {
  if (!tracks.length) return;
  P.queue = tracks;
  P.idx = Math.max(0, start);
  playTrack(P.queue[P.idx], opts);
}

function next(auto = false) {
  if (!P.queue.length) return;
  if (P.shuffle && P.queue.length > 1) {
    let i; do { i = Math.floor(Math.random() * P.queue.length); } while (i === P.idx);
    P.idx = i;
  } else if (P.idx + 1 < P.queue.length) P.idx++;
  else if (P.repeat || !auto) P.idx = 0;
  else { setPlaying(false); return; }
  playTrack(P.queue[P.idx]);
}

function prev() {
  const cur = engineTime();
  if (cur > 3) { seekTo(0); return; }
  if (!P.queue.length) return;
  P.idx = (P.idx - 1 + P.queue.length) % P.queue.length;
  playTrack(P.queue[P.idx]);
}

function engineTime() {
  if (P.engine === "audio") return P.audio.currentTime;
  if (P.engine === "video") return P.video.currentTime;
  if (P.engine === "yt" && P.yt?.getCurrentTime) return P.yt.getCurrentTime();
  return 0;
}

function seekTo(sec) {
  if (P.engine === "audio") P.audio.currentTime = sec;
  else if (P.engine === "video") P.video.currentTime = sec;
  else if (P.engine === "yt" && P.yt) P.yt.seekTo(sec, true);
}

function togglePlay() {
  if (!P.cur) {
    const pl = activePl();
    const items = (pl?.items || []).map((id) => S.tracks.get(id)).filter(Boolean);
    if (items.length) playQueue(items, 0);
    else playQueue(flatTracks(views), 0);
    return;
  }
  if (!P.engine) { playTrack(P.cur); return; } // rien de chargé (échec / bloqué) : on relance
  if (P.engine === "audio") P.audio.paused ? P.audio.play() : P.audio.pause();
  else if (P.engine === "video") P.video.paused ? P.video.play() : P.video.pause();
  else if (P.engine === "yt" && P.yt) P.playing ? P.yt.pauseVideo() : P.yt.playVideo();
}

let seeking = false;
function updateSeek(cur, dur) {
  if (seeking) return;
  $("#p-time").textContent = fmtTime(cur);
  $("#p-dur").textContent = fmtTime(dur);
  $("#p-seek").value = dur ? Math.round((cur / dur) * 1000) : 0;
}

function engineDuration() {
  if (P.engine === "audio") return P.audio.duration || 0;
  if (P.engine === "video") return P.video.duration || 0;
  if (P.engine === "yt" && P.yt?.getDuration) return P.yt.getDuration();
  return 0;
}

for (const el of [P.audio, P.video]) {
  // garde-fou : on n'écoute que l'élément de la source en cours (pas le son de déblocage)
  const mine = () => P.curSrc && el.getAttribute("src") === P.curSrc;
  el.addEventListener("timeupdate", () => { if (mine()) updateSeek(el.currentTime, el.duration); });
  el.addEventListener("playing", () => { if (mine()) { P.started = true; setLoading(false); setPlaying(true); } });
  el.addEventListener("pause", () => { if (mine()) setPlaying(false); });
  el.addEventListener("ended", () => { if (mine()) next(true); });
  el.addEventListener("error", () => { if (mine()) tryNextSource("fichier illisible"); });
}

/* ------------------------------------------------------------------ progression */
function renderSync() {
  const p = S.progress;
  const el = $("#sync");
  if (!p.series) { el.textContent = ""; return; }
  const total = p.themes + p.ost;
  if (p.busy) {
    const bits = [];
    if (p.at_pending) bits.push(`OP/ED ${p.series - p.at_pending}/${p.series} animes`);
    else if (p.ost_pending) bits.push(`OST ${p.series - p.ost_pending}/${p.series}`);
    if (p.spotify_pending) bits.push(`Spotify ${p.spotify_found}/${total}`);
    if (p.youtube_pending) bits.push(`YouTube ${p.themes - p.youtube_pending}/${p.themes}`);
    el.textContent = "Recherche… " + bits.join(" · ");
    el.className = "sync busy";
    el.title = Object.values(p.workers || {}).join("\n");
  } else {
    el.textContent = `${p.themes} thèmes · ${p.ost} pistes OST`;
    el.className = "sync done";
    el.title = `${p.spotify_found} sur Spotify, ${p.youtube_found} sur YouTube`;
  }
}

let pollTimer, lastSig = "", lastLoad = Date.now();
async function poll() {
  clearTimeout(pollTimer);
  try {
    const p = await api("/api/progress");
    const sig = [p.series_pending, p.themes, p.ost, p.spotify_found, p.youtube_found, p.deezer_pending].join(",");
    S.progress = p;
    renderSync();
    // pendant la 1re récupération ça change sans arrêt : on ne redessine qu'une fois / minute
    // (sauf si la liste est encore vide), pour ne pas gêner la navigation ni charger le Pi.
    const due = !S.tracks.size || Date.now() - lastLoad > 60000 || !p.busy;
    if (sig !== lastSig && due && !document.hidden) {
      lastSig = sig;
      lastLoad = Date.now();
      await load(true);
    }
    pollTimer = setTimeout(poll, p.busy ? 6000 : 60000);
  } catch {
    pollTimer = setTimeout(poll, 20000);
  }
}

/* ------------------------------------------------------------------ événements */
$("#list").addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-act]");
  if (!btn) return;
  const act = btn.dataset.act;
  const row = btn.closest(".trk");
  const t = row ? S.tracks.get(+row.dataset.id) : null;
  const sec = btn.closest(".anime");
  const v = sec ? views.find((x) => x.a.id === +sec.dataset.anime) : null;
  const albumEl = btn.closest(".album");
  const albumView = albumEl && v ? v.albums.find((x) => x.al.id === +albumEl.dataset.album) : null;
  if (act === "play" && t) {
    if (P.cur && P.cur.id === t.id) { togglePlay(); return; }
    const flat = flatTracks(views);
    playQueue(flat, flat.indexOf(t));
  } else if (act === "add" && t) toggleTrack(t);
  else if (act === "video" && t) {
    P.queue = [t]; P.idx = 0;
    playTrack(t, { video: true });
  } else if (act === "edit" && t) openLinks(t);
  else if (act === "play-anime" && v) playQueue([...v.seasons.flatMap((x) => x.tracks), ...v.albums.flatMap((x) => x.tracks)]);
  else if (act === "add-anime" && v) addTracks([...v.seasons.flatMap((x) => x.tracks), ...v.albums.flatMap((x) => x.tracks)], v.a.title);
  else if (act === "collapse" && sec) {
    sec.classList.toggle("closed");
    const id = +sec.dataset.anime;
    S.closed.has(id) ? S.closed.delete(id) : S.closed.add(id);
    store("closed", [...S.closed]);
  } else if (act === "play-album" && albumView) { ev.preventDefault(); playQueue(albumView.tracks); }
  else if (act === "add-album" && albumView) { ev.preventDefault(); addTracks(albumView.tracks, albumView.al.title); }
});

$("#list").addEventListener("toggle", (ev) => {
  const d = ev.target;
  if (!d.matches?.("details.album")) return;
  const id = +d.dataset.album;
  if (d.open) {
    S.openAlbums.add(id);
    const v = views.find((x) => x.a.id === +d.closest(".anime").dataset.anime);
    const av = v?.albums.find((x) => x.al.id === id);
    if (av) $(".al-body", d).innerHTML = av.tracks.map((t) => rowHTML(t, activeSet())).join("");
  } else S.openAlbums.delete(id);
}, true);

function saveFilters() { store("filters", { ...S.f, q: "" }); }

$$(".chip[data-kind]").forEach((c) => {
  c.classList.toggle("on", S.f.kinds.includes(c.dataset.kind));
  c.addEventListener("click", () => {
    const k = c.dataset.kind;
    S.f.kinds = S.f.kinds.includes(k) ? S.f.kinds.filter((x) => x !== k) : [...S.f.kinds, k];
    c.classList.toggle("on");
    saveFilters(); renderList();
  });
});
for (const [id, key] of [["#f-avail", "avail"], ["#f-status", "status"]]) {
  const seg = $(id);
  $$("button", seg).forEach((b) => b.classList.toggle("on", b.dataset.v === S.f[key]));
  seg.addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    $$("button", seg).forEach((x) => x.classList.toggle("on", x === b));
    S.f[key] = b.dataset.v;
    saveFilters(); renderList();
  });
}
$("#sort").value = S.f.sort;
$("#sort").addEventListener("change", (e) => { S.f.sort = e.target.value; saveFilters(); renderList(); });
$("#f-notin").checked = S.f.notin;
$("#f-notin").addEventListener("change", (e) => { S.f.notin = e.target.checked; saveFilters(); renderList(); });
let qTimer;
$("#q").addEventListener("input", (e) => {
  clearTimeout(qTimer);
  qTimer = setTimeout(() => { S.f.q = e.target.value.trim(); renderList(); }, 150);
});

$("#add-visible").addEventListener("click", () => {
  const inPl = activeSet();
  const tracks = flatTracks(views).filter((t) => !inPl.has(t.id));
  if (tracks.length > 300 && !confirm(`Ajouter ${tracks.length} titres à « ${activePl().name} » ?`)) return;
  addTracks(tracks, "tout ce qui est affiché");
});

$("#btn-sync").addEventListener("click", async (e) => {
  const b = e.currentTarget;
  b.classList.add("spin");
  try {
    await api("/api/sync", {});
    toast("Je re-vérifie ta liste (nouvelles saisons, nouveaux OP)…");
    poll();
  } catch (err) { toast(esc(err.message), true); }
  setTimeout(() => b.classList.remove("spin"), 1500);
});

/* panneau */
$("#pl-select").addEventListener("change", async (e) => {
  S.active = +e.target.value;
  renderList(); renderPanel();
  try { await api("/api/active", { id: S.active }); } catch (err) { toast(esc(err.message), true); }
});
$("#pl-new").addEventListener("click", async () => {
  const name = prompt("Nom de la nouvelle playlist :", "Ma playlist anime 2");
  if (!name) return;
  try {
    const r = await api("/api/playlists", { name });
    S.playlists = r.playlists; S.active = r.active;
    renderList(); renderPanel();
  } catch (err) { toast(esc(err.message), true); }
});
$("#pl-rename").addEventListener("click", async () => {
  const pl = activePl();
  const name = prompt("Renommer la playlist :", pl.name);
  if (!name || name === pl.name) return;
  try { const r = await api(`/api/playlists/${pl.id}/rename`, { name }); S.playlists = r.playlists; renderPanel(); }
  catch (err) { toast(esc(err.message), true); }
});
$("#pl-delete").addEventListener("click", async () => {
  const pl = activePl();
  if (!confirm(`Supprimer « ${pl.name} » (${pl.items.length} titres) ? La playlist Spotify éventuelle n'est pas touchée.`)) return;
  try {
    const r = await api(`/api/playlists/${pl.id}/delete`, {});
    S.playlists = r.playlists; S.active = r.active;
    renderList(); renderPanel();
  } catch (err) { toast(esc(err.message), true); }
});
const plTracks = () => (activePl()?.items || []).map((id) => S.tracks.get(id)).filter(Boolean);
$("#pl-play").addEventListener("click", () => { P.shuffle = false; $("#p-shuffle").classList.remove("on"); playQueue(plTracks(), 0); });
$("#pl-shuffle").addEventListener("click", () => {
  const items = plTracks();
  P.shuffle = true; $("#p-shuffle").classList.add("on");
  playQueue(items, Math.floor(Math.random() * items.length));
});

$("#pl-items").addEventListener("click", (ev) => {
  const li = ev.target.closest("li[data-id]");
  if (!li) return;
  const id = +li.dataset.id;
  const act = ev.target.closest("[data-act]")?.dataset.act;
  if (act === "rm") removeTracks([id]);
  else if (act === "play-pl") { const items = plTracks(); playQueue(items, items.findIndex((t) => t.id === id)); }
});

let dragId = null;
$("#pl-items").addEventListener("dragstart", (e) => {
  const li = e.target.closest("li[data-id]");
  if (!li) return;
  dragId = +li.dataset.id;
  li.classList.add("drag");
  e.dataTransfer.effectAllowed = "move";
});
$("#pl-items").addEventListener("dragover", (e) => {
  const li = e.target.closest("li[data-id]");
  if (!li || dragId === null) return;
  e.preventDefault();
  $$("#pl-items li.over").forEach((x) => x.classList.remove("over"));
  li.classList.add("over");
});
$("#pl-items").addEventListener("dragend", () => {
  dragId = null;
  $$("#pl-items li.drag, #pl-items li.over").forEach((x) => x.classList.remove("drag", "over"));
});
$("#pl-items").addEventListener("drop", async (e) => {
  const li = e.target.closest("li[data-id]");
  if (!li || dragId === null) return;
  e.preventDefault();
  const pl = activePl();
  const target = +li.dataset.id;
  const ids = pl.items.filter((x) => x !== dragId);
  ids.splice(ids.indexOf(target), 0, dragId);
  pl.items = ids;
  renderPanel();
  try { const r = await api(`/api/playlists/${pl.id}/order`, { ids }); pl.items = r.items; }
  catch (err) { toast(esc(err.message), true); }
});

/* export */
$("#pl-spotify").addEventListener("click", async (e) => {
  if (!S.spotify.configured) {
    toast("La création automatique n'est pas encore branchée (app Spotify à configurer côté serveur). En attendant : « Copier les liens Spotify » puis Ctrl+V dans une playlist de l'app Spotify.", true, 7000);
    return;
  }
  if (!S.spotify.connected) { location.href = "/spotify/login"; return; }
  const b = e.currentTarget;
  b.disabled = true;
  const pl = activePl();
  try {
    const r = await api(`/api/playlists/${pl.id}/spotify`, {});
    pl.spotify_id = r.url.split("/").pop();
    renderPanel();
    toast(`✓ ${r.count} titres envoyés sur Spotify${r.missing ? ` (${r.missing} pas trouvés là-bas)` : ""} — <a class="toast-link" href="${esc(r.url)}" target="_blank" rel="noopener">ouvrir la playlist</a>`, false, 9000);
  } catch (err) { toast(esc(err.message), true, 6000); }
  b.disabled = false;
});

$("#panel").addEventListener("click", async (e) => {
  if (e.target.closest('[data-act="sp-logout"]')) {
    try { await api("/spotify/logout", {}); S.spotify.connected = false; renderPanel(); } catch (err) { toast(esc(err.message), true); }
  }
});

$("#pl-copy").addEventListener("click", async () => {
  const links = [...new Set(plTracks().filter((t) => t.sp).map((t) => `https://open.spotify.com/track/${t.sp}`))];
  try {
    await navigator.clipboard.writeText(links.join("\n"));
    toast(`${links.length} liens Spotify copiés. Dans l'app Spotify (ordinateur) : ouvre une playlist, clique dans la liste et fais <b>Ctrl+V</b>.`, false, 8000);
  } catch { toast("Copie refusée par le navigateur", true); }
});

$("#pl-yt").addEventListener("click", () => {
  const ids = [...new Set(plTracks().filter((t) => t.yt).map((t) => t.yt))];
  const parts = [];
  for (let i = 0; i < ids.length; i += 50) parts.push(ids.slice(i, i + 50));
  $("#yt-parts").innerHTML = parts.map((p, i) =>
    `<a class="btn yt wide" target="_blank" rel="noopener" href="https://www.youtube.com/watch_videos?video_ids=${p.join(",")}">
      ${use("i-yt")} Partie ${i + 1} — titres ${i * 50 + 1} à ${i * 50 + p.length}</a>`).join("");
  $("#yt-dlg").showModal();
});

/* liens manuels */
let editing = null;
function openLinks(t) {
  editing = t;
  const f = $("#links-form");
  $("#links-what").textContent = `${t.anime ? t.anime.title + " · " : ""}${t.s || t.k} — ${t.t} (${t.a || "artiste inconnu"})`;
  f.spotify.value = t.sp ? `https://open.spotify.com/track/${t.sp}` : "";
  f.youtube.value = t.yt ? `https://www.youtube.com/watch?v=${t.yt}` : "";
  $("#links-dlg").showModal();
}
$("#links-dlg").addEventListener("close", async () => {
  if ($("#links-dlg").returnValue !== "save" || !editing) return;
  const f = $("#links-form");
  try {
    const r = await api(`/api/tracks/${editing.id}/links`, { spotify: f.spotify.value, youtube: f.youtube.value });
    Object.assign(editing, { sp: r.track.sp, ss: r.track.ss, yt: r.track.yt, ys: r.track.ys });
    renderList(); renderPanel();
    toast("Liens enregistrés ✓");
  } catch (err) { toast(esc(err.message), true); }
});

/* lecteur : contrôles */
$("#p-play").addEventListener("click", togglePlay);
$("#p-next").addEventListener("click", () => next(false));
$("#p-prev").addEventListener("click", prev);
$("#p-shuffle").addEventListener("click", (e) => { P.shuffle = !P.shuffle; e.currentTarget.classList.toggle("on", P.shuffle); });
$("#p-repeat").addEventListener("click", (e) => { P.repeat = !P.repeat; e.currentTarget.classList.toggle("on", P.repeat); });
$("#p-seek").addEventListener("input", () => { seeking = true; });
$("#p-seek").addEventListener("change", (e) => {
  seeking = false;
  const dur = engineDuration();
  if (dur) seekTo((e.target.value / 1000) * dur);
});
$("#p-vol").value = P.vol;
$("#p-vol").addEventListener("input", (e) => {
  P.vol = +e.target.value;
  P.audio.volume = P.video.volume = P.vol / 100;
  if (P.yt?.setVolume) P.yt.setVolume(P.vol);
  store("vol", P.vol);
});
function renderMode() { $$("#p-mode button").forEach((b) => b.classList.toggle("on", b.dataset.mode === P.mode)); }
renderMode();
$("#p-mode").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  P.mode = b.dataset.mode;
  P.showVideo = P.mode === "video";
  store("mode", P.mode);
  renderMode();
  if (P.cur) playTrack(P.cur);
  else renderDock();
});
$("#dock-min").addEventListener("click", () => $("#dock").classList.toggle("min"));
// ✕ masque seulement la vidéo : la musique continue (▣ dans le lecteur pour la revoir)
$("#dock-close").addEventListener("click", () => { P.showVideo = false; renderDock(); });
$("#p-vid").addEventListener("click", () => { P.showVideo = !P.showVideo; renderDock(); });

/* tiroir playlist (tablette / mobile) */
$("#btn-panel").addEventListener("click", () => $("#panel").classList.toggle("open"));
$("#panel-close").addEventListener("click", () => $("#panel").classList.remove("open"));

document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, select, textarea, dialog")) return;
  if (e.code === "Space") { e.preventDefault(); togglePlay(); }
  else if (e.key === "/") { e.preventDefault(); $("#q").focus(); }
  else if (e.key === "ArrowRight" && e.shiftKey) next(false);
  else if (e.key === "ArrowLeft" && e.shiftKey) prev();
});

if ("mediaSession" in navigator) {
  navigator.mediaSession.setActionHandler("play", togglePlay);
  navigator.mediaSession.setActionHandler("pause", togglePlay);
  navigator.mediaSession.setActionHandler("nexttrack", () => next(false));
  navigator.mediaSession.setActionHandler("previoustrack", prev);
}

/* retour de Spotify */
const qs = new URLSearchParams(location.search);
if (qs.get("spotify") === "ok") toast("Compte Spotify connecté ✓ — tu peux créer la playlist.");
if (qs.get("spotify") === "refus") toast("Connexion Spotify annulée", true);
if (qs.has("spotify")) history.replaceState(null, "", "/");

/* Lien depuis une fiche d'anime.dwa.gold (music.dwa.gold/#a-<id>) : on ouvre cette série,
   quitte à remettre les filtres qui la masqueraient. */
function jumpToHash() {
  const m = /^#a-(\d+)$/.exec(location.hash);
  if (!m) return;
  const id = +m[1];
  if (!S.lib.some((a) => a.id === id)) return;
  if (S.closed.delete(id)) store("closed", [...S.closed]);
  let sec = document.getElementById(`a-${id}`);
  if (!sec) {
    Object.assign(S.f, { status: "all", avail: "all", notin: false, q: "" });
    $("#q").value = "";
    $("#f-notin").checked = false;
    for (const [sel, key] of [["#f-avail", "avail"], ["#f-status", "status"]]) {
      $$(`${sel} button`).forEach((b) => b.classList.toggle("on", b.dataset.v === S.f[key]));
    }
    renderList();
    sec = document.getElementById(`a-${id}`);
  }
  if (!sec) return;
  sec.classList.remove("closed");
  sec.scrollIntoView({ block: "start" });
  sec.classList.add("flash");
  setTimeout(() => sec.classList.remove("flash"), 2200);
}

load().then(() => {
  jumpToHash();
  poll();
  loadYT().catch(() => {}); // lecteur YouTube prêt d'avance : le 1er ▶ démarre tout de suite
});
