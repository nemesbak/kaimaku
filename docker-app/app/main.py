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
from urllib import request
from urllib.error import HTTPError, URLError

import imageio_ffmpeg
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
BACKUP_DIR = DATA_DIR / "backups"
WORK_DIR = DATA_DIR / "work"

MEDIA_ROOTS = [Path(p) for p in os.environ.get("MEDIA_ROOTS", "/media").split(",") if p.strip()]

THEME_AUDIO = Path("theme-music/song1.mp3")
THEME_VIDEO = Path("backdrops/intro.mp4")


def now_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def clean_name(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", " ", value)
    return re.sub(r"\s+", " ", value).strip() or "unknown"


def safe_rel(path: Path) -> str:
    return str(path).replace("\\", "/")


def ensure_inside_roots(path: Path) -> Path:
    resolved = path.resolve()
    for root in MEDIA_ROOTS:
        try:
            resolved.relative_to(root.resolve())
            return resolved
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail=f"path outside configured media roots: {path}")


def media_root_name(path: Path) -> str | None:
    resolved = path.resolve()
    for root in MEDIA_ROOTS:
        try:
            resolved.relative_to(root.resolve())
            return root.name
        except ValueError:
            continue
    return None


def media_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for root in MEDIA_ROOTS:
        if not root.exists():
            continue
        for child in sorted(p for p in root.iterdir() if p.is_dir()):
            if child.name.startswith("_"):
                continue
            kind = "movie" if any(p.is_file() and p.suffix.lower() in {".mkv", ".mp4", ".avi", ".mov"} for p in child.iterdir()) else "series"
            items.append(
                {
                    "id": safe_rel(child.resolve()),
                    "name": child.name,
                    "path": safe_rel(child.resolve()),
                    "root": safe_rel(root.resolve()),
                    "library": root.name,
                    "kind": kind,
                    "has_audio": (child / THEME_AUDIO).is_file(),
                    "has_video": (child / THEME_VIDEO).is_file(),
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
    "{title} trailer oficial español",
]

ANIME_LIBRARY_HINTS = ("anime", "animacion")

OFFICIAL_CHANNEL_TERMS = ("crunchyroll", "vizmedia", "aniplex", "toho", "netflix anime", "muse asia", "funimation")


def is_anime_library(library_name: str | None) -> bool:
    if not library_name:
        return True
    name = library_name.lower()
    return any(hint in name for hint in ANIME_LIBRARY_HINTS)


def build_auto_queries(name: str, anime: bool = True) -> list[str]:
    templates = ANIME_QUERY_TEMPLATES if anime else GENERIC_QUERY_TEMPLATES
    return [t.format(title=name) for t in templates]


def score_auto_candidate(name: str, year: int | None, video: dict[str, Any]) -> float:
    title = normalize(video.get("title") or "")
    channel = normalize(video.get("uploader") or video.get("channel") or "")
    item_name = normalize(name)
    score = 0.0
    if item_name and item_name in title:
        score += 0.35
    if any(term in title for term in ("opening", "op ", "theme", "pv", "tema", "intro", "soundtrack", "banda sonora")):
        score += 0.25
    elif "trailer" in title:
        score += 0.12
    if any(term in title for term in ("español", "espanol", "castellano", "spanish", "latino")):
        score += 0.18
    if any(term in title for term in ("official", "crunchyroll", "aniplex", "toho", "netflix", "oficial")):
        score += 0.15
    if any(term in channel for term in OFFICIAL_CHANNEL_TERMS):
        score += 0.20
    if year and str(year) in title:
        score += 0.05
    duration = video.get("duration") or 0
    if duration and duration <= 360:
        score += 0.10
    if duration and duration > 360:
        score -= 0.25
    if any(term in title for term in ("reaction", "cover", "piano", "amv", "nightcore", "review")):
        score -= 0.30
    return max(0.0, min(1.0, score))


def run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=cwd, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )


class JobCancelled(Exception):
    pass


running_processes: dict[str, subprocess.Popen[str]] = {}
cancelled_jobs: set[str] = set()


def run_cancelable(cmd: list[str], job: "Job") -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(cmd, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    running_processes[job.id] = proc
    try:
        while True:
            if job.id in cancelled_jobs:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise JobCancelled()
            try:
                stdout, stderr = proc.communicate(timeout=0.4)
                if job.id in cancelled_jobs:
                    raise JobCancelled()
                return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    finally:
        running_processes.pop(job.id, None)


def yt_dlp_json(url: str) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "yt_dlp", "--dump-single-json", "--no-playlist", url]
    proc = run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "yt-dlp metadata failed")
    return json.loads(proc.stdout)


def yt_dlp_json_cancelable(job: "Job", url: str) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "yt_dlp", "--dump-single-json", "--no-playlist", url]
    proc = run_cancelable(cmd, job)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "yt-dlp metadata failed")
    return json.loads(proc.stdout)


def ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def download_audio(job: "Job", url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".download.%(ext)s")
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--ffmpeg-location",
        ffmpeg_exe(),
        "-f",
        "bestaudio/best",
        "--extract-audio",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "-o",
        str(temp),
        url,
    ]
    job_log(job, "Descargando audio y convirtiendo a mp3")
    proc = run_cancelable(cmd, job)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "audio download failed")
    found = sorted(target.parent.glob(target.stem + ".download.*"), key=lambda p: p.stat().st_mtime)
    if not found:
        raise RuntimeError("audio output not found")
    found[-1].replace(target)


def download_video(job: "Job", url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".download.%(ext)s")
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--ffmpeg-location",
        ffmpeg_exe(),
        "-f",
        "bv*[height<=720]+ba/b[height<=720]/best",
        "--merge-output-format",
        "mp4",
        "-o",
        str(temp),
        url,
    ]
    job_log(job, "Descargando video y convirtiendo a mp4")
    proc = run_cancelable(cmd, job)
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


def find_library_id(base_url: str, api_key: str, library_name: str) -> str | None:
    req = request.Request(base_url.rstrip("/") + "/Library/VirtualFolders", method="GET")
    req.add_header("X-Emby-Token", api_key)
    with request.urlopen(req, timeout=15) as resp:
        folders = json.loads(resp.read())
    for folder in folders:
        for loc in folder.get("Locations") or []:
            if Path(loc.replace("\\", "/")).name == library_name:
                return folder.get("ItemId") or folder.get("Id")
    return None


def refresh_servers(library_name: str) -> list[dict[str, Any]]:
    """Refresh only the Jellyfin/Emby library whose folder matches `library_name`.

    Deliberately scoped (not /Library/Refresh) so installing one theme doesn't
    trigger a full-server rescan across every library. Matched by folder name
    rather than full path, since Jellyfin/Emby containers can mount the same
    media share at different internal paths.
    """
    results: list[dict[str, Any]] = []
    for name, url_key, token_key in [
        ("jellyfin", "JELLYFIN_URL", "JELLYFIN_API_KEY"),
        ("emby", "EMBY_URL", "EMBY_API_KEY"),
    ]:
        token = os.environ.get(token_key, "").strip()
        base = os.environ.get(url_key, "").strip()
        if not token or not base:
            continue
        try:
            library_id = find_library_id(base, token, library_name)
        except (HTTPError, URLError) as exc:
            results.append({"name": name, "ok": False, "error": str(exc)})
            continue
        if not library_id:
            results.append({"name": name, "ok": False, "error": "no matching library"})
            continue
        req = request.Request(
            base.rstrip("/") + f"/Items/{library_id}/Refresh"
            "?metadataRefreshMode=ValidationOnly&imageRefreshMode=ValidationOnly&replaceAllMetadata=false&replaceAllImages=false&recursive=true",
            method="POST",
        )
        req.add_header("X-Emby-Token", token)
        try:
            with request.urlopen(req, timeout=20) as resp:
                results.append({"name": name, "ok": True, "status": resp.status})
        except HTTPError as exc:
            results.append({"name": name, "ok": False, "status": exc.code})
        except URLError as exc:
            results.append({"name": name, "ok": False, "error": str(exc.reason)})
    return results


class PreviewRequest(BaseModel):
    url: str


class SearchRequest(BaseModel):
    query: str
    limit: int = 8


class AutoSearchRequest(BaseModel):
    destination: str
    limit: int = 8


class JobRequest(BaseModel):
    url: str
    destination: str
    assets: list[Literal["audio", "video"]] = Field(default_factory=lambda: ["audio", "video"])
    refresh: bool = True


class AutopilotRequest(BaseModel):
    library: str | None = None
    destination: str | None = None
    assets: list[Literal["audio", "video"]] = Field(default_factory=lambda: ["audio", "video"])
    min_score: float = 0.75
    overwrite: bool = False
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


@dataclass
class AutopilotRun:
    id: str
    scope: str
    total: int
    status: str = "running"
    processed: int = 0
    queued: list[str] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


autopilot_runs: dict[str, AutopilotRun] = {}
autopilot_cancelled: set[str] = set()


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


def autopilot_log(run: AutopilotRun, message: str) -> None:
    run.logs.append(f"{datetime.now().strftime('%H:%M:%S')} {message}")
    run.updated_at = time.time()
    emit_event({"type": "autopilot", "run": asdict(run)})


def needed_assets(item: dict[str, Any], assets: list[str], overwrite: bool) -> list[str]:
    if overwrite:
        return list(assets)
    return [a for a in assets if not (a == "audio" and item["has_audio"]) and not (a == "video" and item["has_video"])]


def autopilot_worker(run_id: str, items: list[dict[str, Any]], req: AutopilotRequest) -> None:
    run = autopilot_runs[run_id]
    for item in items:
        if run_id in autopilot_cancelled:
            run.status = "cancelled"
            autopilot_log(run, "Autopiloto cancelado")
            break

        run.processed += 1
        needed = needed_assets(item, req.assets, req.overwrite)
        if not needed:
            run.skipped.append({"name": item["name"], "reason": "ya instalado"})
            autopilot_log(run, f"Omitido (ya instalado): {item['name']}")
            continue

        try:
            _, _, candidates = auto_search_candidates(Path(item["path"]))
        except Exception as exc:
            run.skipped.append({"name": item["name"], "reason": str(exc)})
            autopilot_log(run, f"Error buscando {item['name']}: {exc}")
            continue

        best = candidates[0] if candidates else None
        if not best or best["score"] < req.min_score:
            pct = round(best["score"] * 100) if best else 0
            run.skipped.append({"name": item["name"], "reason": f"mejor candidato {pct}% < umbral"})
            autopilot_log(run, f"Omitido (sin candidato fiable, {pct}%): {item['name']}")
            continue

        job = _enqueue(best["webpage_url"], item["path"], needed, req.refresh)
        run.queued.append(job.id)
        autopilot_log(run, f"Encolado ({round(best['score'] * 100)}%): {item['name']} <- {best['title']}")
        time.sleep(1.5)

    autopilot_cancelled.discard(run_id)
    if run.status == "running":
        run.status = "done"
        autopilot_log(run, f"Autopiloto completado: {len(run.queued)} encolados, {len(run.skipped)} omitidos")


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
            destination = ensure_inside_roots(Path(job.destination))
            work = WORK_DIR / job.id
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir(parents=True, exist_ok=True)

            metadata = yt_dlp_json_cancelable(job, job.url)
            job.result["title"] = metadata.get("title")
            job.result["webpage_url"] = metadata.get("webpage_url") or job.url
            job_log(job, f"Fuente: {metadata.get('title') or job.url}")

            installed: list[dict[str, Any]] = []
            backups: list[str] = []
            job.result["installed"] = installed
            job.result["backups"] = backups

            if "audio" in job.assets:
                staged_audio = work / "song1.mp3"
                download_audio(job, job.url, staged_audio)
                target = destination / THEME_AUDIO
                backup = backup_existing(target, destination)
                if backup:
                    backups.append(backup)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(staged_audio, target)
                installed.append({"asset": "audio", "target": safe_rel(target)})
                job_log(job, f"Audio instalado: {THEME_AUDIO}")

            if "video" in job.assets:
                staged_video = work / "intro.mp4"
                download_video(job, job.url, staged_video)
                target = destination / THEME_VIDEO
                backup = backup_existing(target, destination)
                if backup:
                    backups.append(backup)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(staged_video, target)
                installed.append({"asset": "video", "target": safe_rel(target)})
                job_log(job, f"Video instalado: {THEME_VIDEO}")

            library_name = media_root_name(destination)
            refresh = refresh_servers(library_name) if library_name and job.result.get("refresh", True) else []
            job.result["refresh"] = refresh
            if job.assets and refresh:
                job_log(job, "Bibliotecas refrescadas")
            job.status = "done"
            job_log(job, "Job completado")
        except JobCancelled:
            job.status = "cancelled"
            job_log(job, "Job cancelado")
        except Exception as exc:
            job.status = "failed"
            job_log(job, f"ERROR: {exc}")
            # Best-effort: if part of the job (e.g. audio) already made it to disk before
            # the failure, still refresh the library so that partial install shows up.
            if job.result.get("installed"):
                try:
                    library_name = media_root_name(ensure_inside_roots(Path(job.destination)))
                    if library_name and job.result.get("refresh", True):
                        job.result["refresh"] = refresh_servers(library_name)
                        job_log(job, "Bibliotecas refrescadas (instalación parcial)")
                except Exception:
                    pass
        finally:
            cancelled_jobs.discard(job.id)
            running_processes.pop(job.id, None)
            job.updated_at = time.time()
            emit_event({"type": "job", "job": asdict(job)})
            job_queue.task_done()


app = FastAPI(title="Kaimaku")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def startup() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=worker_loop, daemon=True)
    thread.start()


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/items")
def list_items() -> dict[str, Any]:
    return {"roots": [safe_rel(p) for p in MEDIA_ROOTS], "items": media_items()}


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
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        f"ytsearch{limit}:{query}",
        "--dump-json",
        "--no-playlist",
        "--flat-playlist",
    ]
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


def _auto_search_one_query(query: str) -> list[dict[str, Any]]:
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "ytsearch5:" + query,
        "--dump-json",
        "--no-playlist",
        "--flat-playlist",
    ]
    proc = run(cmd)
    if proc.returncode != 0:
        return []
    videos = []
    for line in proc.stdout.splitlines():
        if line.strip():
            videos.append(json.loads(line))
    return videos


def auto_search_candidates(destination: Path, limit: int = 8) -> tuple[str, int | None, list[dict[str, Any]]]:
    name, year = parse_folder_name(destination.name)
    anime = is_anime_library(media_root_name(destination))
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
                        "score": round(score_auto_candidate(name, year, video), 3),
                        "query": query,
                    }
                )
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return name, year, candidates[: max(1, min(limit, 15))]


@app.post("/api/auto-search")
def auto_search(req: AutoSearchRequest) -> dict[str, Any]:
    dest = ensure_inside_roots(Path(req.destination))
    name, year, results = auto_search_candidates(dest, req.limit)
    return {"name": name, "year": year, "results": results}


def _enqueue(url: str, destination: str, assets: list[str], refresh: bool) -> Job:
    job = Job(id=uuid.uuid4().hex[:12], url=url, destination=destination, assets=list(assets))
    job.result["refresh"] = refresh
    jobs[job.id] = job
    job_queue.put(job.id)
    emit_event({"type": "job", "job": asdict(job)})
    return job


@app.post("/api/jobs")
def create_job(req: JobRequest) -> dict[str, Any]:
    ensure_inside_roots(Path(req.destination))
    if not req.assets:
        raise HTTPException(status_code=400, detail="select at least one asset")
    job = _enqueue(req.url, req.destination, req.assets, req.refresh)
    return {"job": asdict(job)}


@app.post("/api/autopilot")
def start_autopilot(req: AutopilotRequest) -> dict[str, Any]:
    if not req.assets:
        raise HTTPException(status_code=400, detail="select at least one asset")

    if req.destination:
        target = ensure_inside_roots(Path(req.destination))
        items = [i for i in media_items() if i["path"] == safe_rel(target)]
        scope = items[0]["name"] if items else target.name
    elif req.library:
        items = [i for i in media_items() if i["library"] == req.library]
        scope = f"biblioteca {req.library}"
    else:
        raise HTTPException(status_code=400, detail="especifica library o destination")

    if not items:
        raise HTTPException(status_code=404, detail="no se encontraron destinos para ese ámbito")

    pending = [i for i in items if needed_assets(i, req.assets, req.overwrite)]
    already_done = len(items) - len(pending)
    if not pending:
        raise HTTPException(
            status_code=400,
            detail=f"los {len(items)} destinos de este ámbito ya tienen instalado lo seleccionado",
        )

    run = AutopilotRun(id=uuid.uuid4().hex[:12], scope=scope, total=len(pending))
    autopilot_runs[run.id] = run
    if already_done:
        autopilot_log(run, f"{already_done} de {len(items)} ya tenían audio/video instalado — excluidos del recuento")
    emit_event({"type": "autopilot", "run": asdict(run)})
    thread = threading.Thread(target=autopilot_worker, args=(run.id, pending, req), daemon=True)
    thread.start()
    return {"run": asdict(run)}


@app.get("/api/autopilot")
def list_autopilot() -> dict[str, Any]:
    return {"runs": [asdict(run) for run in sorted(autopilot_runs.values(), key=lambda r: r.created_at, reverse=True)]}


@app.get("/api/autopilot/{run_id}")
def get_autopilot(run_id: str) -> dict[str, Any]:
    run = autopilot_runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return {"run": asdict(run)}


@app.post("/api/autopilot/{run_id}/cancel")
def cancel_autopilot(run_id: str) -> dict[str, Any]:
    run = autopilot_runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status != "running":
        raise HTTPException(status_code=400, detail="el autopiloto ya ha terminado")
    autopilot_cancelled.add(run_id)
    return {"run": asdict(run)}


@app.get("/api/jobs")
def list_jobs() -> dict[str, Any]:
    return {"jobs": [asdict(job) for job in sorted(jobs.values(), key=lambda j: j.created_at, reverse=True)]}


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


@app.get("/api/events")
async def events() -> StreamingResponse:
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
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
