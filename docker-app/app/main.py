from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib import parse as urlparse, request
from urllib.error import HTTPError, URLError

import imageio_ffmpeg
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from app import setup_core as sc
except ImportError:  # ejecutado fuera del paquete (pruebas)
    import setup_core as sc  # type: ignore[no-redef]

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
BACKUP_DIR = DATA_DIR / "backups"
WORK_DIR = DATA_DIR / "work"
STATE_FILE = DATA_DIR / "state.json"

MEDIA_BASE = Path(os.environ.get("MEDIA_BASE", "/media"))
_media_roots_env = os.environ.get("MEDIA_ROOTS", "").strip()
_explicit_media_roots = [Path(p) for p in _media_roots_env.split(",") if p.strip()] or None

THEME_AUDIO = Path("theme-music/song1.mp3")
THEME_VIDEO = Path("backdrops/intro.mp4")


def discover_media_roots() -> list[Path]:
    """Modo clasico: cada subcarpeta de /media es una biblioteca."""
    if not MEDIA_BASE.is_dir():
        return []
    try:
        return sorted(
            (p for p in MEDIA_BASE.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))),
            key=lambda p: p.name.lower(),
        )
    except OSError:
        return []


def configured_libraries() -> list[dict[str, Any]]:
    """Bibliotecas elegidas en el asistente de la web (modo /host)."""
    return [lib for lib in sc.load_config()["libraries"] if lib.get("enabled", True) and lib.get("path")]


def current_media_roots() -> list[Path]:
    libs = configured_libraries()
    if libs:
        return [Path(lib["path"]) for lib in libs]
    if _explicit_media_roots is not None:
        return _explicit_media_roots
    return discover_media_roots()


def library_ref(root: Path) -> dict[str, Any]:
    """Nombre visible y biblioteca equivalente en Jellyfin/Emby de una carpeta raiz."""
    key = safe_rel(root)
    for lib in configured_libraries():
        if lib["path"] == key:
            return {"label": lib.get("label") or root.name, "servers": lib.get("servers") or {}, "root": root}
    return {"label": root.name, "servers": {}, "root": root}


def root_for(path: Path) -> Path | None:
    resolved = path.resolve()
    for root in current_media_roots():
        try:
            resolved.relative_to(root.resolve())
            return root
        except ValueError:
            continue
    return None


def now_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def clean_name(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", " ", value)
    return re.sub(r"\s+", " ", value).strip() or "unknown"


def safe_rel(path: Path) -> str:
    return str(path).replace("\\", "/")


def ensure_inside_roots(path: Path) -> Path:
    resolved = path.resolve()
    for root in current_media_roots():
        try:
            resolved.relative_to(root.resolve())
            return resolved
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail=f"path outside configured media roots: {path}")


def media_root_name(path: Path) -> str | None:
    root = root_for(path)
    return root.name if root else None


def adopt_owner(path: Path, like: Path) -> None:
    """Sin PUID (modo automatico) Kaimaku corre como root: cada carpeta/archivo que
    crea pasa a ser del mismo dueño que la carpeta de la serie/pelicula, asi
    Sonarr/Radarr/Jellyfin pueden moverlos o borrarlos sin 'Access denied'."""
    if os.geteuid() != 0:
        return
    try:
        st = like.stat()
        os.chown(path, st.st_uid, st.st_gid)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Persisted state (survives restarts): per-item install history + review queue
# ---------------------------------------------------------------------------

_state_lock = threading.Lock()
_state: dict[str, Any] = {}


def load_state() -> None:
    global _state
    if STATE_FILE.exists():
        try:
            _state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            _state = {}
    _state.setdefault("items", {})


def save_state() -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def item_state(item_id: str) -> dict[str, Any]:
    with _state_lock:
        return dict(_state["items"].get(item_id, {}))


def update_item_state(item_id: str, **fields: Any) -> None:
    with _state_lock:
        entry = _state["items"].setdefault(item_id, {})
        entry.update(fields)
        save_state()


def clear_review(item_id: str) -> None:
    with _state_lock:
        entry = _state["items"].get(item_id)
        if entry:
            entry["needs_review"] = False
            entry["candidates"] = []
            entry["last_error"] = None
            save_state()


# ---------------------------------------------------------------------------
# Library scan
# ---------------------------------------------------------------------------

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts"}


def media_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for root in current_media_roots():
        if not root.exists():
            continue
        try:
            children = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        label = library_ref(root)["label"]
        for child in children:
            if child.name.startswith("_"):
                continue
            try:
                kind = "movie" if any(p.is_file() and p.suffix.lower() in VIDEO_EXT for p in child.iterdir()) else "series"
            except OSError:
                continue
            item_id = safe_rel(child.resolve())
            state = item_state(item_id)
            items.append(
                {
                    "id": item_id,
                    "name": child.name,
                    "path": item_id,
                    "root": safe_rel(root),
                    "library": label,
                    "kind": kind,
                    "has_audio": (child / THEME_AUDIO).is_file(),
                    "has_video": (child / THEME_VIDEO).is_file(),
                    "needs_review": bool(state.get("needs_review")),
                    "last_scanned": state.get("last_scanned"),
                    "last_error": state.get("last_error"),
                    "audio_source": state.get("audio_source"),
                    "video_source": state.get("video_source"),
                }
            )
    return items


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def parse_folder_name(folder_name: str) -> tuple[str, int | None]:
    year_match = re.search(r"\((\d{4})(?:-\d{4})?\)", folder_name)
    name = re.sub(r"\s*\[(?:tmdbid|tvdbid|imdbid|imdb)-[^\]]+\]\s*", " ", folder_name, flags=re.I)
    name = re.sub(r"\s*\(\d{4}(?:-\d{4})?\)\s*", " ", name)
    return clean_name(name), int(year_match.group(1)) if year_match else None


ANIME_QUERY_TEMPLATES = [
    "{title} opening español castellano",
    "{title} opening castellano",
    "{title} anime opening español",
    "{title} anime op español latino",
    "{title} anime opening creditless",
    "{title} anime opening official",
]

GENERIC_QUERY_TEMPLATES = [
    "{title} tema principal español",
    "{title} banda sonora tema oficial",
    "{title} soundtrack theme song",
    "{title} tema de entrada español",
    "{title} intro tema musical",
    "{title} banda sonora original español",
]

ANIME_LIBRARY_HINTS = ("anime", "animacion")
# Solo plataformas que suben cabeceras/openings reales. Los canales de estudios
# (Warner, Sony, Paramount, Disney Channel...) suben sobre todo trailers, spots
# de 15-30 s y videoclips: probado 2026-10-06, darles bonus colaba eso como tema.
OFFICIAL_CHANNEL_TERMS = (
    "crunchyroll", "vizmedia", "aniplex", "toho", "netflix", "muse asia", "funimation",
    "prime video", "hbo", "apple tv",
)


def is_anime_library(library_name: str | None) -> bool:
    if not library_name:
        return True
    name = library_name.lower()
    return any(hint in name for hint in ANIME_LIBRARY_HINTS)


def build_auto_queries(name: str, anime: bool = True) -> list[str]:
    templates = ANIME_QUERY_TEMPLATES if anime else GENERIC_QUERY_TEMPLATES
    return [t.format(title=name) for t in templates]


# Terminos ya normalizados (sin tildes, minusculas). Se comparan por palabra
# completa con has_term(): antes era substring y "op " o "pv" casaban dentro
# de cualquier palabra.
THEME_TERMS = ("opening", "op", "theme", "pv", "tema", "intro", "soundtrack", "banda sonora", "main title", "main titles")
# Mas especificos que "theme": casi siempre son la cabecera real, no una pista suelta de la BSO.
TITLE_SEQUENCE_TERMS = ("opening credits", "title sequence", "main title", "main titles", "main theme", "creditless", "cabecera")
SPAIN_TERMS = ("castellano", "espana", "spain")
SPANISH_TERMS = ("espanol", "spanish")
LATINO_TERMS = ("latino", "latin spanish", "latinoamerica", "latam", "mexico")
# Subtitulos/letra en espanol != audio en espanol (2026-10-06: "Mire Kay -
# Industry (Sub Espanol / Lyrics) | DARK" llego a 1.0 por esto).
SUBS_TERMS = ("sub", "sub espanol", "subtitulado", "subtitulada", "lyrics", "letra", "traducida", "traducido")
OFFICIAL_TITLE_TERMS = ("official", "oficial", "crunchyroll", "aniplex", "toho")
JUNK_TERMS = (
    "reaction", "cover", "piano", "amv", "nightcore", "review", "trailer", "teaser", "clip", "escena", "scene",
    "completa", "full movie", "pelicula completa", "fandub", "shorts", "parody", "parodia", "fanmade", "fan made",
    "spot", "tv spot", "anuncio", "avance", "promo", "featurette", "videoclip", "making of", "como se hizo", "behind the scenes",
    "style", "homage", "reimagined", "fan edit",
)
SEASON_RE = re.compile(r"\b(?:season|temporada|s)\s?0*([2-9]|\d{2})\b")
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


def has_term(text: str, terms: tuple[str, ...]) -> bool:
    return any(re.search(r"\b" + re.escape(term) + r"\b", text) for term in terms)


def score_auto_candidate(name: str, year: int | None, video: dict[str, Any], anime: bool = True) -> float:
    title = normalize(video.get("title") or "")
    channel = normalize(video.get("uploader") or video.get("channel") or "")
    item_name = normalize(name)
    score = 0.0
    if item_name and re.search(r"\b" + re.escape(item_name) + r"\b", title):
        # Nombres cortos/genericos ("Industry", "Ted 2") aparecen dentro de
        # titulos de cualquier cosa: solo puntuan del todo si el video EMPIEZA
        # por el nombre; si no, cuentan poco.
        generic = len(item_name.split()) <= 2 and len(item_name) <= 12
        score += 0.35 if (not generic or title.startswith(item_name)) else 0.15
    if has_term(title, THEME_TERMS):
        score += 0.25
    if has_term(title, TITLE_SEQUENCE_TERMS):
        score += 0.10
    subs = has_term(title, SUBS_TERMS)
    if has_term(title, LATINO_TERMS) or has_term(channel, LATINO_TERMS):
        score -= 0.15  # se prefiere castellano; latino solo si no hay nada mejor
    elif subs:
        score -= 0.05
    elif has_term(title, SPAIN_TERMS):
        score += 0.18
    elif has_term(title, SPANISH_TERMS):
        score += 0.10
    if has_term(title, OFFICIAL_TITLE_TERMS):
        score += 0.15
    # "Netflix"/"Prime" en el TITULO no prueban nada (cualquiera lo escribe);
    # solo cuenta si el canal es el oficial.
    if any(term in channel for term in OFFICIAL_CHANNEL_TERMS):
        score += 0.20
    title_years = {int(y) for y in YEAR_RE.findall(title)}
    if year and year in title_years:
        score += 0.05
    elif year and any(y < year - 1 for y in title_years):
        score -= 0.25  # obra anterior con el mismo nombre (Spartacus 2010 vs Kubrick 1960)
    if SEASON_RE.search(title):
        score -= 0.05  # a igualdad, mejor el opening original que el de una temporada posterior
    duration = video.get("duration") or 0
    if duration and duration <= 360:
        score += 0.10
    if duration and duration > 360:
        score -= 0.25
    if has_term(title, JUNK_TERMS) or (not anime and has_term(title, ("anime",))):
        score -= 0.30  # "anime opening" fuera de una biblioteca de anime = fan-made ("Cars 3 Anime Opening")
    return max(0.0, min(1.0, score))


def run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=cwd, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )


class JobCancelled(Exception):
    pass


running_processes: dict[str, subprocess.Popen[str]] = {}
cancelled_jobs: set[str] = set()


def run_cancelable(cmd: list[str], job_id: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(cmd, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    running_processes[job_id] = proc
    try:
        while True:
            if job_id in cancelled_jobs:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise JobCancelled()
            try:
                stdout, stderr = proc.communicate(timeout=1)
                return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    finally:
        running_processes.pop(job_id, None)


# Firma exacta vista repetidamente durante escaneos largos (2026-08-24 y
# 2026-08-25, 5 videos distintos): la API de YouTube devuelve 403 bajo el
# volumen de peticiones de un escaneo completo, yt-dlp cae a "solo imagenes
# disponibles" y el formato pedido ya no existe. Es transitorio -- probado a
# mano varias veces, el MISMO video se descarga bien minutos despues sin
# cambiar nada. Un reintento con espera cubre la mayoria de los casos sin
# alargar mucho un fallo real (video borrado/restringido de verdad).
TRANSIENT_YT_ERROR_RE = re.compile(r"HTTP Error 403|Unable to download API page", re.I)


def _yt_dlp_download(cmd: list[str], job_id: str, log: Any, retry_wait: int = 25) -> subprocess.CompletedProcess[str]:
    proc = run_cancelable(cmd, job_id)
    if proc.returncode != 0 and TRANSIENT_YT_ERROR_RE.search(proc.stderr or ""):
        log(f"YouTube devolvio 403 (transitorio bajo carga alta); reintentando en {retry_wait}s")
        time.sleep(retry_wait)
        proc = run_cancelable(cmd, job_id)
    return proc


def yt_dlp_json(url: str) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "yt_dlp", "--dump-single-json", "--no-playlist", url]
    proc = run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "yt-dlp metadata failed")
    return json.loads(proc.stdout)


def ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def download_audio(job_id: str, url: str, target: Path, log: Any) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".download.%(ext)s")
    cmd = [
        sys.executable, "-m", "yt_dlp", "--no-playlist",
        "--ffmpeg-location", ffmpeg_exe(),
        "-f", "bestaudio/best",
        "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0",
        "-o", str(temp), url,
    ]
    log("Descargando audio y convirtiendo a mp3")
    proc = _yt_dlp_download(cmd, job_id, log)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "audio download failed")
    found = sorted(target.parent.glob(target.stem + ".download.*"), key=lambda p: p.stat().st_mtime)
    if not found:
        raise RuntimeError("audio output not found")
    found[-1].replace(target)


def download_video(job_id: str, url: str, target: Path, log: Any) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".download.%(ext)s")
    cmd = [
        sys.executable, "-m", "yt_dlp", "--no-playlist",
        "--ffmpeg-location", ffmpeg_exe(),
        "-f", "bv*[height<=720]+ba/b[height<=720]/best",
        "--merge-output-format", "mp4",
        "-o", str(temp), url,
    ]
    log("Descargando video y convirtiendo a mp4")
    proc = _yt_dlp_download(cmd, job_id, log)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "video download failed")
    found = sorted(target.parent.glob(target.stem + ".download.*"), key=lambda p: p.stat().st_mtime)
    if not found:
        raise RuntimeError("video output not found")
    found[-1].replace(target)


def backup_existing(target: Path, item_dir: Path) -> str | None:
    if not target.exists():
        return None
    rel = target.relative_to(item_dir)
    backup = BACKUP_DIR / now_id() / item_dir.name / rel
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup)
    return safe_rel(backup)


def _auto_search_one_query(query: str) -> list[dict[str, Any]]:
    cmd = [sys.executable, "-m", "yt_dlp", "ytsearch5:" + query, "--dump-json", "--no-playlist", "--flat-playlist"]
    proc = run(cmd)
    if proc.returncode != 0:
        return []
    videos = []
    for line in proc.stdout.splitlines():
        if line.strip():
            try:
                videos.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return videos


def auto_search_candidates(destination: Path, limit: int = 8) -> tuple[str, int | None, list[dict[str, Any]]]:
    name, year = parse_folder_name(destination.name)
    root = root_for(destination)
    # La carpeta (anime/) o el nombre de la biblioteca en Jellyfin ("Anime") deciden.
    anime = root is None or is_anime_library(root.name) or is_anime_library(library_ref(root)["label"])
    queries = build_auto_queries(name, anime)

    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        for query, videos in zip(queries, pool.map(_auto_search_one_query, queries)):
            for video in videos:
                video_id = video.get("id")
                if not video_id or video_id in seen:
                    continue
                seen.add(video_id)
                thumbnails = video.get("thumbnails") or []
                thumbnail = (
                    video.get("thumbnail")
                    or (thumbnails[-1].get("url") if thumbnails else None)
                    or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                )
                candidates.append(
                    {
                        "id": video_id,
                        "title": video.get("title"),
                        "uploader": video.get("uploader") or video.get("channel"),
                        "duration": video.get("duration"),
                        "thumbnail": thumbnail,
                        "webpage_url": video.get("webpage_url") or video.get("url") or f"https://www.youtube.com/watch?v={video_id}",
                        "embed_url": f"https://www.youtube.com/embed/{video_id}",
                        "score": round(score_auto_candidate(name, year, video, anime), 3),
                        "query": query,
                    }
                )
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return name, year, candidates[: max(1, min(limit, 15))]


# ---------------------------------------------------------------------------
# Jellyfin / Emby integration: reachability, refresh, poster proxy
# ---------------------------------------------------------------------------

def _servers() -> list[dict[str, Any]]:
    return sc.configured_servers()


def find_library_id(base_url: str, token: str, library_name: str) -> str | None:
    """Modo clasico: la biblioteca del servidor cuya carpeta se llama igual que la nuestra."""
    folders = sc.server_call(base_url, "/Library/VirtualFolders", token=token) or []
    for folder in folders:
        for loc in folder.get("Locations") or []:
            if Path(loc.replace("\\", "/")).name == library_name:
                return folder.get("ItemId") or folder.get("Id")
    return None


def check_server_reachable(base_url: str) -> bool:
    return sc.public_info(base_url, timeout=4) is not None


def servers_status() -> list[dict[str, Any]]:
    out = []
    for srv in _servers():
        ok_auth = False
        try:
            sc.server_call(srv["url"], "/Library/VirtualFolders", token=srv["token"], timeout=8)
            ok_auth = True
        except Exception:
            pass
        out.append({
            "kind": srv["kind"], "url": srv["url"], "user": srv.get("user"), "name": srv.get("name"),
            "source": srv.get("source"), "reachable": ok_auth or check_server_reachable(srv["url"]), "auth": ok_auth,
        })
    return out


def roots_status() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for root in current_media_roots():
        exists = root.is_dir()
        count = 0
        if exists:
            try:
                count = sum(1 for p in root.iterdir() if p.is_dir() and not p.name.startswith("_"))
            except OSError:
                exists = False
        result.append({"path": safe_rel(root), "name": library_ref(root)["label"], "exists": exists, "items": count})
    return result


def refresh_servers(root: Path | None) -> list[dict[str, Any]]:
    """Scoped refresh (not /Library/Refresh) so installing one theme doesn't
    trigger a full-server rescan across every library."""
    if root is None:
        return []
    ref = library_ref(root)
    results: list[dict[str, Any]] = []
    for srv in _servers():
        name = srv["kind"]
        try:
            library_id = (ref["servers"].get(name) or {}).get("id") or find_library_id(srv["url"], srv["token"], root.name)
        except (HTTPError, URLError) as exc:
            results.append({"name": name, "ok": False, "error": str(exc)})
            continue
        if not library_id:
            results.append({"name": name, "ok": False, "error": "no matching library"})
            continue
        try:
            sc.server_call(
                srv["url"],
                f"/Items/{library_id}/Refresh?metadataRefreshMode=ValidationOnly&imageRefreshMode=ValidationOnly"
                "&replaceAllMetadata=false&replaceAllImages=false&recursive=true",
                token=srv["token"], method="POST", timeout=20,
            )
            results.append({"name": name, "ok": True, "status": 204})
        except HTTPError as exc:
            results.append({"name": name, "ok": False, "status": exc.code})
        except URLError as exc:
            results.append({"name": name, "ok": False, "error": str(exc.reason)})
    return results


_poster_index_cache: dict[tuple[str, str], dict[str, Any]] = {}
_poster_index_at: float = 0.0
_poster_index_lock = threading.Lock()
POSTER_INDEX_TTL = 600.0


def _fetch_poster_index_for(base: str, token: str) -> dict[tuple[str, str], dict[str, Any]]:
    """(library, folder-name) -> {id, base} for every Movie/Series in the server,
    matched by folder name rather than full path (Jellyfin/Emby may mount the
    same share at a different internal path than Kaimaku)."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    payload = sc.server_call(
        base, "/Items?Recursive=true&IncludeItemTypes=Movie,Series&Fields=Path&EnableImages=true&Limit=100000",
        token=token, timeout=30,
    ) or {}
    for entry in payload.get("Items") or []:
        raw_path = entry.get("Path")
        if not raw_path:
            continue
        p = Path(raw_path.replace("\\", "/"))
        folder = p.name if entry.get("IsFolder", True) else p.parent.name
        library = p.parent.name if entry.get("IsFolder", True) else p.parent.parent.name
        has_image = bool((entry.get("ImageTags") or {}).get("Primary"))
        out[(library.lower(), folder.lower())] = {"id": entry.get("Id"), "base": base, "token": token, "has_image": has_image}
    return out


def poster_index() -> dict[tuple[str, str], dict[str, Any]]:
    global _poster_index_cache, _poster_index_at
    with _poster_index_lock:
        if time.time() - _poster_index_at < POSTER_INDEX_TTL and _poster_index_cache:
            return _poster_index_cache
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for srv in _servers():
            try:
                merged.update(_fetch_poster_index_for(srv["url"], srv["token"]))
            except Exception:
                continue
        _poster_index_cache = merged
        _poster_index_at = time.time()
        return merged


def reset_poster_index() -> None:
    global _poster_index_at
    with _poster_index_lock:
        _poster_index_at = 0.0


def find_poster_ref(root: Path, folder_name: str) -> dict[str, Any] | None:
    idx = poster_index()
    # El servidor puede llamar a la carpeta de la biblioteca distinto que aqui
    # (/movies en Jellyfin, /host/pelis en Kaimaku): se prueban todos los nombres.
    names = {root.name.lower()}
    for info in library_ref(root)["servers"].values():
        if info.get("location"):
            names.add(Path(info["location"]).name.lower())
    for library in names:
        entry = idx.get((library, folder_name.lower()))
        if entry and entry.get("has_image"):
            return entry
    return None


# ---------------------------------------------------------------------------
# Telegram notifications (optional — silent if not configured)
# ---------------------------------------------------------------------------

def tg_escape(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def notify_telegram(text: str) -> None:
    bot_token, chat_id, topic_id = sc.setting("tg_bot_token"), sc.setting("tg_chat_id"), sc.setting("tg_topic_id")
    if not bot_token or not chat_id:
        return
    body: dict[str, str] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if topic_id:
        body["message_thread_id"] = topic_id
    data = urlparse.urlencode(body).encode()
    req = request.Request(f"https://api.telegram.org/bot{bot_token}/sendMessage", data=data, method="POST")
    try:
        request.urlopen(req, timeout=15)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Jobs (manual install) + SSE broadcast
# ---------------------------------------------------------------------------

class PreviewRequest(BaseModel):
    url: str


class SearchRequest(BaseModel):
    query: str
    limit: int = 8


class ItemCandidatesRequest(BaseModel):
    limit: int = 8


class JobRequest(BaseModel):
    url: str
    destination: str
    assets: list[Literal["audio", "video"]] = Field(default_factory=lambda: ["audio", "video"])
    refresh: bool = True


@dataclass
class Job:
    id: str
    url: str
    destination: str
    assets: list[str]
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)


jobs: dict[str, Job] = {}
job_queue: "queue.Queue[str]" = queue.Queue()
event_subscribers: list[asyncio.Queue[str]] = []


def emit_event(payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False)
    for subscriber in list(event_subscribers):
        try:
            subscriber.put_nowait(data)
        except asyncio.QueueFull:
            pass


def job_log(job: Job, message: str) -> None:
    job.logs.append(f"{datetime.now().strftime('%H:%M:%S')} {message}")
    job.updated_at = time.time()
    emit_event({"type": "job", "job": asdict(job)})


def run_install(job: Job, source: dict[str, Any] | None = None) -> None:
    """Downloads job.url into job.destination for job.assets, updates persisted
    item state on success, and (if requested) triggers a scoped library refresh."""
    destination = ensure_inside_roots(Path(job.destination))
    item_id = safe_rel(destination)
    work = WORK_DIR / job.id
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    metadata = yt_dlp_json(job.url)
    job.result["title"] = metadata.get("title")
    job.result["webpage_url"] = metadata.get("webpage_url") or job.url
    job_log(job, f"Fuente: {metadata.get('title') or job.url}")

    installed: list[dict[str, Any]] = []
    backups: list[str] = []
    job.result["installed"] = installed
    job.result["backups"] = backups

    src_meta = source or {
        "id": metadata.get("id"),
        "title": metadata.get("title"),
        "uploader": metadata.get("uploader") or metadata.get("channel"),
        "url": metadata.get("webpage_url") or job.url,
        "installed_at": now_iso(),
    }
    src_meta = {**src_meta, "installed_at": now_iso()}

    if "audio" in job.assets:
        staged = work / "song1.mp3"
        download_audio(job.id, job.url, staged, lambda m: job_log(job, m))
        target = destination / THEME_AUDIO
        backup = backup_existing(target, destination)
        if backup:
            backups.append(backup)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged, target)
        adopt_owner(target.parent, destination)
        adopt_owner(target, destination)
        installed.append({"asset": "audio", "target": safe_rel(target)})
        job_log(job, f"Audio instalado: {THEME_AUDIO}")
        update_item_state(item_id, audio_source=src_meta)

    if "video" in job.assets:
        staged = work / "intro.mp4"
        download_video(job.id, job.url, staged, lambda m: job_log(job, m))
        target = destination / THEME_VIDEO
        backup = backup_existing(target, destination)
        if backup:
            backups.append(backup)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged, target)
        adopt_owner(target.parent, destination)
        adopt_owner(target, destination)
        installed.append({"asset": "video", "target": safe_rel(target)})
        job_log(job, f"Video instalado: {THEME_VIDEO}")
        update_item_state(item_id, video_source=src_meta)

    clear_review(item_id)

    root = root_for(destination)
    refresh = refresh_servers(root) if root and job.result.get("refresh", True) else []
    job.result["refresh"] = refresh
    if job.assets and refresh:
        job_log(job, "Bibliotecas refrescadas")


def worker_loop() -> None:
    while True:
        job_id = job_queue.get()
        job = jobs[job_id]
        if job.status == "cancelled":
            job_queue.task_done()
            continue
        try:
            job.status = "running"
            job_log(job, "Job iniciado")
            run_install(job)
            job.status = "done"
            job_log(job, "Completado")
        except JobCancelled:
            job.status = "cancelled"
            job_log(job, "Job cancelado")
        except Exception as exc:
            job.status = "failed"
            job_log(job, f"ERROR: {exc}")
            if job.result.get("installed"):
                try:
                    root = root_for(ensure_inside_roots(Path(job.destination)))
                    if root and job.result.get("refresh", True):
                        job.result["refresh"] = refresh_servers(root)
                        job_log(job, "Bibliotecas refrescadas (instalación parcial)")
                except Exception:
                    pass
        finally:
            cancelled_jobs.discard(job.id)
            running_processes.pop(job.id, None)
            shutil.rmtree(WORK_DIR / job.id, ignore_errors=True)
            job.updated_at = time.time()
            emit_event({"type": "job", "job": asdict(job)})
            job_queue.task_done()


def _enqueue(url: str, destination: str, assets: list[str], refresh: bool) -> Job:
    job = Job(id=uuid.uuid4().hex[:12], url=url, destination=destination, assets=list(assets))
    job.result["refresh"] = refresh
    jobs[job.id] = job
    job_queue.put(job.id)
    emit_event({"type": "job", "job": asdict(job)})
    return job


# ---------------------------------------------------------------------------
# Auto-scan: the background worker that replaces manually-triggered "autopilot".
# Finds items missing audio/video, installs anything confident enough on its
# own, flags the rest for manual review in the UI, and sends one batched
# Telegram summary per run (only when there's something worth reporting).
# ---------------------------------------------------------------------------

@dataclass
class ScanState:
    running: bool = False
    last_started: str | None = None
    last_finished: str | None = None
    last_summary: dict[str, Any] = field(default_factory=dict)


scan_state = ScanState()
scan_lock = threading.Lock()


def run_auto_scan(trigger: str = "schedule") -> dict[str, Any]:
    if not scan_lock.acquire(blocking=False):
        return {"skipped": "already running"}
    try:
        scan_state.running = True
        scan_state.last_started = now_iso()
        emit_event({"type": "scan", "scan": asdict(scan_state)})

        installed_names: list[str] = []
        newly_review_names: list[str] = []
        error_names: list[str] = []
        touched_libraries: set[str] = set()
        auto_assets = sc.setting("assets")
        min_score = sc.setting("min_score")

        for item in media_items():
            needed = [a for a in auto_assets if not item.get(f"has_{a}")]
            if not needed:
                continue
            was_review = item["needs_review"]
            try:
                _, _, candidates = auto_search_candidates(Path(item["path"]))
            except Exception as exc:
                error_names.append(item["name"])
                update_item_state(item["id"], last_scanned=now_iso(), last_error=str(exc))
                emit_event({"type": "scan_progress", "item": item["name"], "outcome": "error", "detail": str(exc)})
                continue

            # Se intenta con TODOS los candidatos que superan el umbral, no solo el
            # mejor: un video puede fallar al descargar (borrado, restringido por
            # edad/region, sin formato valido) aunque su titulo/canal encajen bien,
            # y antes eso hacia que el item se marcara como error sin mas alternativa
            # aunque el candidato #2 o #3 hubiera funcionado perfectamente.
            # Tope de 3 intentos por item para que un item con muchos candidatos
            # empatados en el umbral no alargue el escaneo entero si todos fallan.
            qualifying = [c for c in candidates if c["score"] >= min_score][:3]
            installed_ok = False
            last_error: str | None = None
            for candidate in qualifying:
                job = Job(id=uuid.uuid4().hex[:12], url=candidate["webpage_url"], destination=item["path"], assets=needed)
                job.result["refresh"] = False  # batched at the end of the whole scan instead
                jobs[job.id] = job
                try:
                    run_install(job, source={
                        "id": candidate["id"], "title": candidate["title"], "uploader": candidate.get("uploader"),
                        "url": candidate["webpage_url"],
                    })
                    job.status = "done"
                    installed_names.append(f"{item['name']} ({round(candidate['score'] * 100)}%)")
                    touched_libraries.add(item["root"])
                    emit_event({"type": "scan_progress", "item": item["name"], "outcome": "installed"})
                    installed_ok = True
                    break
                except Exception as exc:
                    job.status = "failed"
                    last_error = str(exc)
                    emit_event({"type": "scan_progress", "item": item["name"], "outcome": "error", "detail": last_error})

            if installed_ok:
                update_item_state(item["id"], last_scanned=now_iso(), last_error=None)
            elif qualifying:
                # Habia al menos un candidato con score suficiente y TODOS
                # fallaron al descargar: eso si es un error real (no solo "nada
                # confiable"). Se deja para revision manual con el motivo real
                # guardado, en vez de un simple "error" sin ninguna pista.
                error_names.append(item["name"])
                update_item_state(
                    item["id"],
                    needs_review=True,
                    candidates=candidates[:5],
                    last_scanned=now_iso(),
                    last_error=last_error,
                )
                if not was_review:
                    newly_review_names.append(item["name"])
                emit_event({"type": "scan_progress", "item": item["name"], "outcome": "review"})
            else:
                # Ningun candidato llego al umbral: no se intento descargar nada,
                # asi que no se toca last_error (no borrar el motivo de un fallo
                # real de un escaneo anterior solo porque esta vez la busqueda
                # no encontro nada igual de confiable).
                update_item_state(
                    item["id"],
                    needs_review=True,
                    candidates=candidates[:5],
                    last_scanned=now_iso(),
                )
                if not was_review:
                    newly_review_names.append(item["name"])
                emit_event({"type": "scan_progress", "item": item["name"], "outcome": "review"})

        for root in touched_libraries:
            refresh_servers(Path(root))

        summary = {
            "trigger": trigger,
            "installed": installed_names,
            "new_review": newly_review_names,
            "errors": error_names,
            "finished_at": now_iso(),
        }
        scan_state.last_finished = now_iso()
        scan_state.last_summary = summary
        emit_event({"type": "scan", "scan": asdict(scan_state)})

        if installed_names or newly_review_names or error_names:
            lines = [f"🎬 <b>Kaimaku — escaneo automático</b>", ""]
            if installed_names:
                lines.append(f"✅ Instalados ({len(installed_names)}):")
                lines += [f"• {tg_escape(n)}" for n in installed_names[:15]]
                if len(installed_names) > 15:
                    lines.append(f"…y {len(installed_names) - 15} más")
                lines.append("")
            if newly_review_names:
                lines.append(f"👀 Pendientes de revisión ({len(newly_review_names)}):")
                lines += [f"• {tg_escape(n)}" for n in newly_review_names[:15]]
                if len(newly_review_names) > 15:
                    lines.append(f"…y {len(newly_review_names) - 15} más")
                lines.append("")
            if error_names:
                lines.append(f"⚠️ Errores ({len(error_names)}):")
                lines += [f"• {tg_escape(n)}" for n in error_names[:10]]
            notify_telegram("\n".join(lines).strip()[:4000])

        return summary
    finally:
        scan_state.running = False
        scan_lock.release()


def auto_scan_loop() -> None:
    """Relee el intervalo cada minuto: si se cambia en la web se aplica sin
    reiniciar. 0 = escaneo automatico desactivado."""
    time.sleep(120)  # let the app + yt-dlp warm up before the first pass
    last_run = 0.0
    while True:
        interval = sc.setting("scan_interval_hours")
        if interval > 0 and current_media_roots() and time.time() - last_run >= max(1.0, interval) * 3600:
            last_run = time.time()
            try:
                run_auto_scan(trigger="schedule")
            except Exception:
                pass
        time.sleep(60)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Kaimaku")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.on_event("startup")
def startup() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    load_state()
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=worker_loop, daemon=True).start()
    threading.Thread(target=auto_scan_loop, daemon=True).start()


@app.on_event("shutdown")
def shutdown() -> None:
    if not any(j.status == "running" for j in jobs.values()):
        return
    deadline = time.time() + 25
    while any(j.status == "running" for j in jobs.values()) and time.time() < deadline:
        time.sleep(0.5)


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/library")
def get_library() -> dict[str, Any]:
    items = media_items()
    libraries = sorted({i["library"] for i in items}, key=str.lower)
    return {"roots": [safe_rel(p) for p in current_media_roots()], "libraries": libraries, "items": items}


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": sc.VERSION}


@app.get("/api/status")
def get_status() -> dict[str, Any]:
    return {
        "version": sc.VERSION,
        "roots": roots_status(),
        "servers": servers_status(),
        "mounts": sc.mount_status(),
        "running_as": "root (dueño automático)" if os.geteuid() == 0 else f"{os.geteuid()}:{os.getegid()}",
        "telegram": bool(sc.setting("tg_bot_token") and sc.setting("tg_chat_id")),
        "auto_scan": {
            "interval_hours": sc.setting("scan_interval_hours"),
            "min_score": sc.setting("min_score"),
            "assets": sc.setting("assets"),
            **asdict(scan_state),
        },
    }


@app.get("/api/poster")
def get_poster(item: str):
    path = ensure_inside_roots(Path(item))
    root = root_for(path)
    if not root:
        raise HTTPException(status_code=404, detail="not found")
    ref = find_poster_ref(root, path.name)
    if not ref:
        raise HTTPException(status_code=404, detail="no poster")
    url = ref["base"].rstrip("/") + f"/Items/{ref['id']}/Images/Primary?maxWidth=480&quality=90"
    req = request.Request(url, method="GET")
    req.add_header("X-Emby-Token", ref["token"])
    req.add_header("Authorization", f'MediaBrowser Token="{ref["token"]}"')  # Jellyfin 12 ya no acepta X-Emby-Token solo
    try:
        with request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            ctype = resp.headers.get("Content-Type", "image/jpeg")
    except Exception:
        raise HTTPException(status_code=404, detail="fetch failed")
    return Response(content=body, media_type=ctype, headers={"Cache-Control": "public, max-age=86400"})


@app.post("/api/preview")
def preview(req: PreviewRequest) -> dict[str, Any]:
    try:
        data = yt_dlp_json(req.url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    video_id = data.get("id")
    return {
        "id": video_id,
        "title": data.get("title"),
        "uploader": data.get("uploader") or data.get("channel"),
        "duration": data.get("duration"),
        "thumbnail": data.get("thumbnail"),
        "webpage_url": data.get("webpage_url") or req.url,
        "embed_url": f"https://www.youtube.com/embed/{video_id}" if video_id else None,
    }


@app.post("/api/search")
def search_youtube(req: SearchRequest) -> dict[str, Any]:
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="falta el término de búsqueda")
    limit = max(1, min(req.limit, 15))
    cmd = [sys.executable, "-m", "yt_dlp", f"ytsearch{limit}:{query}", "--dump-json", "--no-playlist", "--flat-playlist"]
    proc = run(cmd)
    if proc.returncode != 0:
        raise HTTPException(status_code=400, detail=proc.stderr.strip() or "la búsqueda en YouTube falló")
    results: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        video_id = data.get("id")
        thumbnails = data.get("thumbnails") or []
        thumbnail = data.get("thumbnail") or (thumbnails[-1].get("url") if thumbnails else None)
        results.append(
            {
                "id": video_id,
                "title": data.get("title"),
                "uploader": data.get("uploader") or data.get("channel"),
                "duration": data.get("duration"),
                "thumbnail": thumbnail,
                "webpage_url": data.get("webpage_url") or data.get("url") or f"https://www.youtube.com/watch?v={video_id}",
                "embed_url": f"https://www.youtube.com/embed/{video_id}" if video_id else None,
            }
        )
    return {"results": results}


@app.post("/api/item/candidates")
def item_candidates(item: str, req: ItemCandidatesRequest) -> dict[str, Any]:
    dest = ensure_inside_roots(Path(item))
    name, year, results = auto_search_candidates(dest, req.limit)
    return {"name": name, "year": year, "results": results}


@app.get("/api/item/review")
def item_review(item: str) -> dict[str, Any]:
    dest = ensure_inside_roots(Path(item))
    state = item_state(safe_rel(dest))
    return {"needs_review": bool(state.get("needs_review")), "candidates": state.get("candidates") or []}


@app.post("/api/jobs")
def create_job(req: JobRequest) -> dict[str, Any]:
    ensure_inside_roots(Path(req.destination))
    if not req.assets:
        raise HTTPException(status_code=400, detail="select at least one asset")
    job = _enqueue(req.url, req.destination, req.assets, req.refresh)
    return {"job": asdict(job)}


@app.get("/api/jobs")
def list_jobs() -> dict[str, Any]:
    return {"jobs": [asdict(job) for job in sorted(jobs.values(), key=lambda j: j.created_at, reverse=True)][:200]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return {"job": asdict(job)}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status not in ("queued", "running"):
        raise HTTPException(status_code=400, detail="el job ya ha terminado")
    cancelled_jobs.add(job_id)
    if job.status == "queued":
        job.status = "cancelled"
        job_log(job, "Cancelado antes de empezar")
    else:
        job_log(job, "Cancelación solicitada")
        proc = running_processes.get(job_id)
        if proc:
            proc.terminate()
    return {"job": asdict(job)}


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str) -> dict[str, Any]:
    old = jobs.get(job_id)
    if not old:
        raise HTTPException(status_code=404, detail="job not found")
    job = _enqueue(old.url, old.destination, old.assets, old.result.get("refresh", True))
    return {"job": asdict(job)}


@app.post("/api/scan/run")
def trigger_scan() -> dict[str, Any]:
    if scan_state.running:
        raise HTTPException(status_code=409, detail="ya hay un escaneo en curso")
    threading.Thread(target=run_auto_scan, kwargs={"trigger": "manual"}, daemon=True).start()
    return {"started": True}


@app.get("/api/scan/status")
def scan_status() -> dict[str, Any]:
    return {"scan": asdict(scan_state)}


# ---------------------------------------------------------------------------
# Asistente de configuracion (web)
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    url: str
    username: str = ""
    password: str = ""
    api_key: str = ""


class ForgetRequest(BaseModel):
    kind: Literal["jellyfin", "emby"]


class SaveSetupRequest(BaseModel):
    libraries: list[dict[str, Any]] | None = None  # None = no tocar las bibliotecas (solo ajustes)
    settings: dict[str, Any] = Field(default_factory=dict)


def _public_settings() -> dict[str, Any]:
    out = {k: sc.setting(k) for k in sc.DEFAULT_SETTINGS}
    if out.get("tg_bot_token"):
        out["tg_bot_token"] = "••••••" + out["tg_bot_token"][-4:]
    out["locked"] = [k for k in sc.DEFAULT_SETTINGS if sc.setting_locked(k)]
    return out


@app.get("/api/setup/state")
def setup_state() -> dict[str, Any]:
    cfg = sc.load_config()
    mounts = sc.mount_status()
    legacy = [safe_rel(p) for p in discover_media_roots()] if not configured_libraries() else []
    if mounts["host"]["mounted"]:
        sc.warm_index_async()  # el asistente lo va a necesitar en unos segundos
    return {
        "version": sc.VERSION,
        "needs_setup": not current_media_roots(),
        "mode": "web" if configured_libraries() else ("media" if legacy or _explicit_media_roots else "none"),
        "mounts": mounts,
        "servers": [{k: v for k, v in s.items() if k != "token"} for s in sc.configured_servers()],
        "libraries": cfg["libraries"],
        "legacy_roots": legacy,
        "settings": _public_settings(),
    }


@app.get("/api/setup/discover")
def setup_discover() -> dict[str, Any]:
    return {"servers": sc.discover_servers()}


@app.post("/api/setup/login")
def setup_login(req: LoginRequest) -> dict[str, Any]:
    try:
        if req.api_key.strip():
            server = sc.login_with_key(req.url, req.api_key.strip())
        else:
            if not req.username.strip():
                raise ValueError("Escribe el usuario administrador de tu Jellyfin/Emby.")
            server = sc.login(req.url, req.username.strip(), req.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reset_poster_index()
    return {"server": server}


@app.post("/api/setup/forget")
def setup_forget(req: ForgetRequest) -> dict[str, Any]:
    sc.forget_server(req.kind)
    reset_poster_index()
    return {"ok": True}


@app.post("/api/setup/map")
def setup_map() -> dict[str, Any]:
    if not sc.configured_servers():
        raise HTTPException(status_code=400, detail="Conecta antes tu Jellyfin o Emby.")
    if not sc.search_roots():
        raise HTTPException(status_code=400, detail="Kaimaku no ve ninguna carpeta montada en /host. Revisa la línea del volumen.")
    return sc.map_libraries()


@app.get("/api/setup/browse")
def setup_browse(path: str = "") -> dict[str, Any]:
    try:
        return sc.browse(path or None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/setup/save")
def setup_save(req: SaveSetupRequest) -> dict[str, Any]:
    cfg = sc.load_config()
    libraries: list[dict[str, Any]] = [] if req.libraries is not None else list(cfg["libraries"])
    seen: set[str] = set()
    for lib in req.libraries or []:
        raw = str(lib.get("path") or "")
        path = sc.allowed_path(raw)
        if not path or not path.is_dir():
            raise HTTPException(status_code=400, detail=f"La carpeta «{raw}» no existe dentro de lo montado en Kaimaku.")
        key = safe_rel(path)
        if key in seen:
            continue
        seen.add(key)
        servers = {}
        for kind, info in (lib.get("servers") or {}).items():
            if kind in ("jellyfin", "emby") and isinstance(info, dict):
                servers[kind] = {k: str(info.get(k) or "") for k in ("id", "location", "library")}
        libraries.append({
            "path": key,
            "label": str(lib.get("label") or path.name)[:80],
            "type": str(lib.get("type") or ""),
            "servers": servers,
            "enabled": bool(lib.get("enabled", True)),
        })
    settings = dict(cfg.get("settings") or {})
    for key, value in (req.settings or {}).items():
        if key not in sc.DEFAULT_SETTINGS or sc.setting_locked(key):
            continue
        if key == "scan_interval_hours":
            settings[key] = max(0.0, min(168.0, float(value or 0)))
        elif key == "min_score":
            settings[key] = max(0.3, min(1.0, float(value or 0.75)))
        elif key == "assets":
            settings[key] = [a for a in (value or []) if a in ("audio", "video")] or ["audio", "video"]
        elif key == "tg_bot_token" and str(value).startswith("••••"):
            continue  # el valor enmascarado que mostramos: no se ha cambiado
        else:
            settings[key] = str(value or "").strip()
    sc.save_config({**cfg, "libraries": libraries, "settings": settings})
    reset_poster_index()
    return {"ok": True, "roots": [safe_rel(p) for p in current_media_roots()], "items": len(media_items())}


@app.get("/api/events")
async def events() -> StreamingResponse:
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
    event_subscribers.append(q)

    async def stream():
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                data = await q.get()
                yield f"event: message\ndata: {data}\n\n"
        finally:
            event_subscribers.remove(q)

    return StreamingResponse(stream(), media_type="text/event-stream")
