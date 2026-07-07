#!/usr/bin/env python3
"""
Anime Theme Sync

Prototype CLI to find theme/trailer candidates for anime libraries shared by
Jellyfin and Emby. The default flow is intentionally cautious:

  1. scan     -> collect anime series/items
  2. search   -> create candidate report using yt-dlp search
  3. download -> explicitly download selected candidates
  4. refresh  -> ask Jellyfin/Emby to rescan changed items
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError, URLError


APP_DIR = Path(__file__).resolve().parent
STATE_DIR = APP_DIR / "state"
DEFAULT_SCAN = STATE_DIR / "anime_items.json"
DEFAULT_CANDIDATES = STATE_DIR / "anime_candidates.json"
DEFAULT_CHANGED = STATE_DIR / "changed_items.json"
DEFAULT_STAGE = APP_DIR / "staged"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def clean_name(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or "unknown"


def slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return value[:80] or "unknown"


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def parse_folder_name(folder_name: str) -> tuple[str, int | None, dict[str, str]]:
    provider_ids: dict[str, str] = {}
    provider_aliases = {"tmdbid": "tmdb", "tvdbid": "tvdb", "imdbid": "imdb", "imdb": "imdb"}
    for provider, value in re.findall(r"\[(tmdbid|tvdbid|imdbid|imdb)-([^\]]+)\]", folder_name, flags=re.I):
        provider_ids[provider_aliases[provider.lower()]] = value
    year_match = re.search(r"\((\d{4})(?:-\d{4})?\)", folder_name)
    name = re.sub(r"\s*\[(?:tmdbid|tvdbid|imdbid|imdb)-[^\]]+\]\s*", " ", folder_name, flags=re.I)
    name = re.sub(r"\s*\(\d{4}(?:-\d{4})?\)\s*", " ", name)
    return clean_name(name), int(year_match.group(1)) if year_match else None, provider_ids


def run(cmd: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def run_checked(cmd: list[str]) -> None:
    proc = run(cmd, capture=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"command failed: {' '.join(cmd)}")


def log(message: str) -> None:
    encoding = sys.stdout.encoding or "utf-8"
    print(message.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def require_tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"Missing required tool: {name}")
    return found


def yt_dlp_cmd() -> list[str]:
    return [sys.executable, "-m", "yt_dlp"]


def http_json(method: str, base_url: str, api_key: str, path: str, params: dict[str, Any] | None = None) -> Any:
    qs = params or {}
    url = base_url.rstrip("/") + path
    if qs:
        url += "?" + parse.urlencode({k: v for k, v in qs.items() if v is not None})
    req = request.Request(url, method=method)
    req.add_header("X-Emby-Token", api_key)
    req.add_header("Accept", "application/json")
    try:
        with request.urlopen(req, timeout=45) as resp:
            raw = resp.read()
    except HTTPError as exc:
        raise RuntimeError(f"{method} {url} failed: HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"{method} {url} failed: {exc.reason}") from exc
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


@dataclass
class MediaServer:
    name: str
    url: str
    api_key: str
    enabled: bool = True

    @classmethod
    def from_config(cls, name: str, cfg: dict[str, Any]) -> "MediaServer | None":
        if not cfg or not cfg.get("enabled", True):
            return None
        url = cfg.get("url", "").strip()
        api_key = cfg.get("api_key", "").strip()
        if not url or not api_key or api_key.startswith("REPLACE_"):
            return None
        return cls(name=name, url=url, api_key=api_key, enabled=True)

    def libraries(self) -> list[dict[str, Any]]:
        data = http_json("GET", self.url, self.api_key, "/Library/MediaFolders")
        return data.get("Items", [])

    def items(self, parent_id: str, include_types: str) -> list[dict[str, Any]]:
        data = http_json(
            "GET",
            self.url,
            self.api_key,
            "/Items",
            {
                "Recursive": "true",
                "ParentId": parent_id,
                "IncludeItemTypes": include_types,
                "Fields": "Path,ProviderIds,ProductionYear,OriginalTitle,Overview",
            },
        )
        return data.get("Items", [])

    def refresh(self, item_id: str) -> None:
        http_json(
            "POST",
            self.url,
            self.api_key,
            f"/Items/{item_id}/Refresh",
            {
                "Recursive": "true",
                "MetadataRefreshMode": "Default",
                "ImageRefreshMode": "Default",
                "ReplaceAllMetadata": "false",
                "ReplaceAllImages": "false",
            },
        )


def configured_servers(config: dict[str, Any]) -> list[MediaServer]:
    servers: list[MediaServer] = []
    for name in ("jellyfin", "emby"):
        server = MediaServer.from_config(name, config.get(name, {}))
        if server:
            servers.append(server)
    return servers


def scan_from_servers(config: dict[str, Any]) -> list[dict[str, Any]]:
    wanted_libraries = {normalize(x) for x in config["scan"].get("library_names", ["Anime"])}
    include_types = ",".join(config["scan"].get("include_item_types", ["Series"]))
    merged: dict[str, dict[str, Any]] = {}

    for server in configured_servers(config):
        for library in server.libraries():
            lib_name = library.get("Name", "")
            if wanted_libraries and normalize(lib_name) not in wanted_libraries:
                continue
            for item in server.items(str(library["Id"]), include_types):
                path = item.get("Path") or ""
                key = normalize(path or f"{item.get('Name')} {item.get('ProductionYear')}")
                entry = merged.setdefault(
                    key,
                    {
                        "name": item.get("Name"),
                        "original_title": item.get("OriginalTitle"),
                        "year": item.get("ProductionYear"),
                        "path": path,
                        "provider_ids": item.get("ProviderIds") or {},
                        "servers": {},
                    },
                )
                entry["servers"][server.name] = {"id": item.get("Id"), "library": lib_name}
    return sorted(merged.values(), key=lambda x: normalize(x.get("name") or ""))


def scan_from_paths(config: dict[str, Any]) -> list[dict[str, Any]]:
    roots = [Path(p) for p in config["scan"].get("filesystem_roots", [])]
    items: list[dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            print(f"warn: root not found: {root}", file=sys.stderr)
            continue
        for child in root.iterdir():
            if not child.is_dir():
                continue
            name, year, provider_ids = parse_folder_name(child.name)
            items.append(
                {
                    "name": name,
                    "original_title": None,
                    "year": year,
                    "path": str(child),
                    "provider_ids": provider_ids,
                    "servers": {},
                }
            )
    return sorted(items, key=lambda x: normalize(x.get("name") or ""))


def scan(args: argparse.Namespace) -> None:
    config = load_json(Path(args.config))
    items = scan_from_servers(config)
    if not items:
        items = scan_from_paths(config)
    save_json(Path(args.output), items)
    print(f"scan: wrote {len(items)} anime items to {args.output}")


def yt_search(query: str, limit: int) -> list[dict[str, Any]]:
    cmd = [
        *yt_dlp_cmd(),
        "--dump-single-json",
        "--flat-playlist",
        "--playlist-end",
        str(limit),
        f"ytsearch{limit}:{query}",
    ]
    proc = run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"yt-dlp failed for {query}")
    data = json.loads(proc.stdout)
    return data.get("entries") or []


def candidate_url(video: dict[str, Any]) -> str:
    video_id = video.get("id")
    for key in ("webpage_url", "original_url", "url"):
        value = video.get(key)
        if value and str(value).startswith(("http://", "https://")):
            return str(value)
    return f"https://www.youtube.com/watch?v={video_id}"


def score_candidate(item: dict[str, Any], video: dict[str, Any], config: dict[str, Any]) -> float:
    title = normalize(video.get("title") or "")
    channel = normalize(video.get("channel") or video.get("uploader") or "")
    item_name = normalize(item.get("name") or "")
    original = normalize(item.get("original_title") or "")
    score = 0.0
    if item_name and item_name in title:
        score += 0.35
    if original and original in title:
        score += 0.15
    if any(term in title for term in ("opening", "op ", "theme", "trailer", "pv", "teaser")):
        score += 0.25
    if any(term in title for term in ("español", "espanol", "castellano", "spanish", "sub español", "sub espanol", "latino")):
        score += 0.18
    if any(term in title for term in ("official", "crunchyroll", "aniplex", "toho", "netflix", "kadokawa")):
        score += 0.15
    if any(term in channel for term in config["search"].get("official_channel_terms", [])):
        score += 0.20
    if item.get("year") and str(item["year"]) in title:
        score += 0.05
    duration = video.get("duration") or 0
    max_seconds = int(config["search"].get("max_candidate_seconds", 360))
    if duration and duration <= max_seconds:
        score += 0.10
    if duration and duration > max_seconds:
        score -= 0.25
    if any(term in title for term in ("reaction", "cover", "piano", "amv", "nightcore", "review")):
        score -= 0.30
    return max(0.0, min(1.0, score))


def build_queries(item: dict[str, Any], config: dict[str, Any]) -> list[str]:
    templates = config["search"].get(
        "query_templates",
        [
            "{title} opening español castellano",
            "{title} opening castellano",
            "{title} anime opening español",
            "{title} anime op español castellano",
            "{title} anime opening creditless",
            "{title} anime opening official",
            "{title} main theme anime",
        ],
    )
    title = item.get("original_title") or item.get("name")
    return [t.format(title=title, year=item.get("year") or "").strip() for t in templates]


def search(args: argparse.Namespace) -> None:
    config = load_json(Path(args.config))
    items = load_json(Path(args.input))
    limit = int(config["search"].get("results_per_query", 5))
    min_score = float(config["search"].get("min_score", 0.55))
    report = []

    for index, item in enumerate(items[: args.limit or None], start=1):
        print(f"search: {index}/{len(items)} {item.get('name')}")
        seen: set[str] = set()
        candidates: list[dict[str, Any]] = []
        for query in build_queries(item, config):
            try:
                results = yt_search(query, limit)
            except Exception as exc:
                candidates.append({"query": query, "error": str(exc)})
                continue
            for video in results:
                video_id = video.get("id")
                if not video_id or video_id in seen:
                    continue
                seen.add(video_id)
                score = score_candidate(item, video, config)
                candidates.append(
                    {
                        "score": round(score, 3),
                        "id": video_id,
                        "url": candidate_url(video),
                        "title": video.get("title"),
                        "channel": video.get("channel") or video.get("uploader"),
                        "duration": video.get("duration"),
                        "query": query,
                    }
                )
        candidates = sorted(
            [c for c in candidates if c.get("error") or c.get("score", 0) >= min_score],
            key=lambda x: x.get("score", 0),
            reverse=True,
        )
        report.append({"item": item, "candidates": candidates[: int(config["search"].get("keep_candidates", 5))]})
        time.sleep(float(config["search"].get("delay_seconds", 0.5)))

    save_json(Path(args.output), report)
    print(f"search: wrote candidates to {args.output}")


def init_config(args: argparse.Namespace) -> None:
    src = APP_DIR / "config.example.json"
    dst = Path(args.config)
    if dst.exists() and not args.force:
        raise SystemExit(f"config already exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    print(f"init: wrote {dst}")


def target_files(item: dict[str, Any], config: dict[str, Any]) -> tuple[Path, Path]:
    base = Path(item["path"])
    names = config["download"].get("target_names", {})
    audio_name = names.get("audio", "theme-music/song1.mp3")
    video_name = names.get("video", "backdrops/intro.mp4")
    return base / audio_name, base / video_name


def media_score(candidate: dict[str, Any], media_type: str) -> float:
    title = normalize(candidate.get("title") or "")
    channel = normalize(candidate.get("channel") or "")
    score = float(candidate.get("score") or 0)
    if media_type == "audio":
        if any(term in title for term in ("opening", "op ", "op1", "op 1", "theme", "main theme")):
            score += 0.35
        if any(term in title for term in ("español", "espanol", "castellano", "spanish", "latino")):
            score += 0.25
        if any(term in title for term in ("trailer", "teaser", "pv")):
            score -= 0.20
    else:
        if any(term in title for term in ("opening", "op ", "op1", "op 1", "creditless")):
            score += 0.40
        if any(term in title for term in ("español", "espanol", "castellano", "spanish", "sub español", "sub espanol", "latino")):
            score += 0.30
        if any(term in title for term in ("official trailer", "trailer", "teaser", "pv")):
            score -= 0.20
    if any(term in channel for term in ("crunchyroll", "vizmedia", "aniplex", "toho", "warner bros")):
        score += 0.10
    return score


def choose_candidate(candidates: list[dict[str, Any]], media_type: str) -> dict[str, Any] | None:
    usable = [c for c in candidates if not c.get("error")]
    if not usable:
        return None
    return max(usable, key=lambda c: media_score(c, media_type))


def ranked_candidates(candidates: list[dict[str, Any]], media_type: str) -> list[dict[str, Any]]:
    usable = [c for c in candidates if not c.get("error")]
    return sorted(usable, key=lambda c: media_score(c, media_type), reverse=True)


def download_audio(url: str, target: Path) -> None:
    require_tool("ffmpeg")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_template = str(target.with_suffix(".download.%(ext)s"))
    cmd = [
        *yt_dlp_cmd(),
        "-f",
        "bestaudio/best",
        "--extract-audio",
        "--audio-format",
        target.suffix.lstrip(".") or "mp3",
        "--audio-quality",
        "0",
        "-o",
        temp_template,
        url,
    ]
    run_checked(cmd)
    downloaded = sorted(target.parent.glob(target.stem + ".download.*"), key=lambda p: p.stat().st_mtime)
    if not downloaded:
        raise RuntimeError(f"audio download did not produce {target}")
    downloaded[-1].replace(target)


def download_video(url: str, target: Path) -> None:
    require_tool("ffmpeg")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_template = str(target.with_suffix(".download.%(ext)s"))
    cmd = [
        *yt_dlp_cmd(),
        "--no-playlist",
        "-f",
        "bv*[height<=480]+ba/b[height<=480]/best",
        "--merge-output-format",
        target.suffix.lstrip(".") or "mp4",
        "-o",
        temp_template,
        url,
    ]
    run_checked(cmd)
    downloaded = sorted(target.parent.glob(target.stem + ".download.*"), key=lambda p: p.stat().st_mtime)
    if not downloaded:
        raise RuntimeError(f"video download did not produce {target}")
    downloaded[-1].replace(target)


def download_youtube_backdrop(video_id: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    urls = [
        f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/sddefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
    ]
    last_error = None
    for url in urls:
        try:
            req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            if len(data) < 10_000:
                continue
            target.write_bytes(data)
            return
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"could not download backdrop for {video_id}: {last_error}")


def stage(args: argparse.Namespace) -> None:
    report_data = load_json(Path(args.input))
    stage_root = Path(args.output)
    manifest: list[dict[str, Any]] = []
    min_score = float(args.min_score)
    assets = set(args.assets.split(","))
    selected = 0

    for entry in report_data:
        item = entry["item"]
        candidates = [c for c in entry.get("candidates", []) if not c.get("error")]
        if not item.get("path") or not candidates:
            continue
        best_score = max((float(c.get("score") or 0) for c in candidates), default=0)
        if best_score < min_score:
            continue
        selected += 1
        if args.limit and selected > args.limit:
            break

        item_slug = f"{selected:03d}_{slugify(item.get('name') or 'anime')}"
        item_dir = stage_root / item_slug
        item_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "name": item.get("name"),
            "year": item.get("year"),
            "remote_path": item.get("path"),
            "stage_dir": item_slug,
            "files": [],
            "errors": [],
        }

        if "audio" in assets:
            target = item_dir / "theme-music" / "song1.mp3"
            audio_candidates = ranked_candidates(candidates, "audio")
            if audio_candidates:
                if not target.exists() or args.force:
                    if target.exists():
                        target.unlink()
                    for audio in audio_candidates:
                        try:
                            log(f"stage audio: {item.get('name')} <- {audio.get('title')}")
                            download_audio(audio["url"], target)
                            record["last_audio_candidate"] = audio
                            break
                        except Exception as exc:
                            record["errors"].append({"asset": "audio", "error": str(exc), "candidate": audio})
                    if not target.exists():
                        record["errors"].append({"asset": "audio", "error": "all audio candidates failed"})
                if target.exists():
                    audio = record.get("last_audio_candidate") or audio_candidates[0]
                    record["files"].append({"asset": "audio", "name": "theme-music/song1.mp3", "source": str(target), "candidate": audio})

        if "video" in assets or "backdrop" in assets:
            target = item_dir / "backdrops" / "intro.mp4"
            video_candidates = ranked_candidates(candidates, "video")
            if video_candidates:
                if not target.exists() or args.force:
                    if target.exists():
                        target.unlink()
                    for video in video_candidates:
                        try:
                            log(f"stage video: {item.get('name')} <- {video.get('title')}")
                            download_video(video["url"], target)
                            record["last_video_candidate"] = video
                            break
                        except Exception as exc:
                            record["errors"].append({"asset": "video", "error": str(exc), "candidate": video})
                    if not target.exists():
                        record["errors"].append({"asset": "video", "error": "all video candidates failed"})
                if target.exists():
                    video = record.get("last_video_candidate") or video_candidates[0]
                    record["files"].append({"asset": "video", "name": "backdrops/intro.mp4", "source": str(target), "candidate": video})

        if record["files"] or record["errors"]:
            manifest.append(record)

    save_json(stage_root / "manifest.json", manifest)
    print(f"stage: wrote {len(manifest)} items to {stage_root}")


def download(args: argparse.Namespace) -> None:
    config = load_json(Path(args.config))
    report = load_json(Path(args.input))
    if config["download"].get("convert_with_ffmpeg", True):
        require_tool("ffmpeg")
    overwrite = bool(config["download"].get("overwrite_existing", False))
    changed = []

    for entry in report[: args.limit or None]:
        item = entry["item"]
        if not item.get("path"):
            continue
        chosen = choose_candidate(entry.get("candidates", []), args.media_type)
        if not chosen:
            continue
        audio_file, video_file = target_files(item, config)
        media_type = args.media_type
        target = audio_file if media_type == "audio" else video_file
        if target.exists() and not overwrite:
            print(f"skip: exists {target}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_template = str(target.with_suffix(".download.%(ext)s"))
        url = chosen["url"]
        if media_type == "audio":
            cmd = [
                *yt_dlp_cmd(),
                "-f",
                "bestaudio/best",
                "--extract-audio",
                "--audio-format",
                target.suffix.lstrip(".") or "mp3",
                "--audio-quality",
                "0",
                "-o",
                temp_template,
                url,
            ]
        else:
            cmd = [
                *yt_dlp_cmd(),
                "-f",
                "bv*[height<=720]+ba/b[height<=720]/best",
                "--merge-output-format",
                target.suffix.lstrip(".") or "mp4",
                "-o",
                temp_template,
                url,
            ]
        print(f"download: {item.get('name')} -> {target.name}")
        proc = run(cmd, capture=True)
        if proc.returncode != 0:
            print(proc.stderr, file=sys.stderr)
            continue
        downloaded = sorted(target.parent.glob(target.stem + ".download.*"), key=lambda p: p.stat().st_mtime)
        if downloaded:
            downloaded[-1].replace(target)
            changed.append({"item": item, "target": str(target), "candidate": chosen})

    save_json(Path(args.changed), changed)
    print(f"download: wrote changed item list to {args.changed}")


def refresh(args: argparse.Namespace) -> None:
    config = load_json(Path(args.config))
    changed = load_json(Path(args.input))
    servers = {s.name: s for s in configured_servers(config)}
    count = 0
    for entry in changed:
        item = entry["item"]
        for server_name, server_ref in item.get("servers", {}).items():
            server = servers.get(server_name)
            item_id = server_ref.get("id")
            if not server or not item_id:
                continue
            print(f"refresh: {server.name} {item.get('name')}")
            server.refresh(str(item_id))
            count += 1
    print(f"refresh: requested {count} item refreshes")


def report(args: argparse.Namespace) -> None:
    data = load_json(Path(args.input))
    lines = [
        "# Anime Theme Candidates",
        "",
        f"Items: {len(data)}",
        "",
        "| Anime | Audio pick | Video pick | Candidates |",
        "| --- | --- | --- | --- |",
    ]
    weak: list[str] = []
    for entry in data:
        item = entry["item"]
        candidates = [c for c in entry.get("candidates", []) if not c.get("error")]
        audio = choose_candidate(candidates, "audio")
        video = choose_candidate(candidates, "video")
        best_score = max((c.get("score", 0) for c in candidates), default=0)
        if not candidates or best_score < args.weak_score:
            weak.append(item.get("name") or "unknown")

        def link(candidate: dict[str, Any] | None) -> str:
            if not candidate:
                return "_none_"
            title = str(candidate.get("title") or "candidate").replace("|", "\\|")
            return f"[{title}]({candidate.get('url')})"

        name = str(item.get("name") or "unknown").replace("|", "\\|")
        lines.append(f"| {name} | {link(audio)} | {link(video)} | {len(candidates)} |")

    lines.extend(["", "## Weak Or Empty", ""])
    if weak:
        lines.extend(f"- {name}" for name in weak)
    else:
        lines.append("- None")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report: wrote {output}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Find and install anime theme media for Jellyfin/Emby.")
    parser.add_argument("--config", default=str(APP_DIR / "config.json"))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="Create config.json from config.example.json.")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=init_config)

    p = sub.add_parser("scan", help="Scan anime items from Jellyfin/Emby or filesystem roots.")
    p.add_argument("--output", default=str(DEFAULT_SCAN))
    p.set_defaults(func=scan)

    p = sub.add_parser("search", help="Search YouTube candidates via yt-dlp without downloading.")
    p.add_argument("--input", default=str(DEFAULT_SCAN))
    p.add_argument("--output", default=str(DEFAULT_CANDIDATES))
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=search)

    p = sub.add_parser("download", help="Download the top candidate for each item.")
    p.add_argument("--input", default=str(DEFAULT_CANDIDATES))
    p.add_argument("--changed", default=str(DEFAULT_CHANGED))
    p.add_argument("--media-type", choices=["audio", "video"], default="audio")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=download)

    p = sub.add_parser("stage", help="Stage theme-music/song1.mp3 and backdrops/intro.mp4 locally.")
    p.add_argument("--input", default=str(DEFAULT_CANDIDATES))
    p.add_argument("--output", default=str(DEFAULT_STAGE))
    p.add_argument("--assets", default="audio,video")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--min-score", type=float, default=0.75)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=stage)

    p = sub.add_parser("refresh", help="Refresh changed Jellyfin/Emby items.")
    p.add_argument("--input", default=str(DEFAULT_CHANGED))
    p.set_defaults(func=refresh)

    p = sub.add_parser("report", help="Create a Markdown report from candidate JSON.")
    p.add_argument("--input", default=str(DEFAULT_CANDIDATES))
    p.add_argument("--output", default=str(APP_DIR / "anime_candidates_report.md"))
    p.add_argument("--weak-score", type=float, default=0.75)
    p.set_defaults(func=report)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
