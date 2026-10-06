"""Configuracion guiada de Kaimaku: servidores Jellyfin/Emby, bibliotecas y ajustes.

Objetivo: que nadie tenga que saber rutas internas, API keys ni PUID/PGID.
- El usuario monta una carpeta GRANDE (p. ej. /mnt/user en Unraid) en /host.
- Se conecta a Jellyfin/Emby con usuario y contrasena (Kaimaku saca su propio token).
- Kaimaku pide al servidor sus bibliotecas y localiza cada una dentro de /host
  comparando los nombres de carpeta que conoce el servidor con lo que hay en disco
  (da igual que Jellyfin las vea en /media/peliculas y aqui esten en
  /host/datos/media/peliculas, o que esten repartidas en varios discos).

Todo se guarda en /data/config.json. Las variables de entorno antiguas
(JELLYFIN_URL, EMBY_API_KEY, AUTO_*, TG_*...) siguen funcionando y tienen prioridad.
"""
from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError

VERSION = "1.6.0"

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CONFIG_FILE = DATA_DIR / "config.json"
HOST_BASE = Path(os.environ.get("HOST_BASE", "/host"))
MEDIA_BASE = Path(os.environ.get("MEDIA_BASE", "/media"))

DEFAULT_SETTINGS: dict[str, Any] = {
    "scan_interval_hours": 12,
    "min_score": 0.75,
    "assets": ["audio", "video"],
    "tg_bot_token": "",
    "tg_chat_id": "",
    "tg_topic_id": "",
}
# setting -> variable de entorno que, si tiene valor, manda sobre lo guardado en la web
SETTING_ENV = {
    "scan_interval_hours": "AUTO_SCAN_INTERVAL_HOURS",
    "min_score": "AUTO_MIN_SCORE",
    "assets": "AUTO_ASSETS",
    "tg_bot_token": "TG_BOT_TOKEN",
    "tg_chat_id": "TG_CHAT_ID",
    "tg_topic_id": "TG_TOPIC_ID",
}

# Tipos de biblioteca de Jellyfin/Emby donde los temas tienen sentido.
VIDEO_COLLECTION_TYPES = {"movies", "tvshows", "mixed", "", None}

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".wmv", ".m2ts", ".webm"}

# ---------------------------------------------------------------------------
# Config persistida
# ---------------------------------------------------------------------------

_cfg_lock = threading.Lock()
_cfg: dict[str, Any] | None = None


def load_config() -> dict[str, Any]:
    global _cfg
    with _cfg_lock:
        if _cfg is None:
            try:
                _cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                _cfg = {}
            _cfg.setdefault("device_id", "kaimaku-" + uuid.uuid4().hex[:16])
            _cfg.setdefault("servers", [])
            _cfg.setdefault("libraries", [])
            _cfg.setdefault("settings", {})
        return _cfg


def save_config(cfg: dict[str, Any]) -> None:
    global _cfg
    with _cfg_lock:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_FILE)
        try:
            os.chmod(CONFIG_FILE, 0o600)  # guarda tokens: solo legible por Kaimaku
        except OSError:
            pass
        _cfg = cfg


def setting(key: str) -> Any:
    env_name = SETTING_ENV.get(key)
    raw = os.environ.get(env_name, "").strip() if env_name else ""
    default = DEFAULT_SETTINGS[key]
    value: Any = raw if raw else load_config()["settings"].get(key, default)
    try:
        if key == "scan_interval_hours":
            return float(value)
        if key == "min_score":
            return float(value)
        if key == "assets":
            if isinstance(value, str):
                value = [a.strip() for a in value.split(",")]
            return [a for a in value if a in ("audio", "video")] or ["audio", "video"]
        return str(value or "").strip()
    except (TypeError, ValueError):
        return default


def setting_locked(key: str) -> bool:
    env_name = SETTING_ENV.get(key)
    return bool(env_name and os.environ.get(env_name, "").strip())


# ---------------------------------------------------------------------------
# Servidores Jellyfin / Emby
# ---------------------------------------------------------------------------

ENV_SERVERS = {"jellyfin": ("JELLYFIN_URL", "JELLYFIN_API_KEY"), "emby": ("EMBY_URL", "EMBY_API_KEY")}


def configured_servers() -> list[dict[str, Any]]:
    """[{kind, url, token, source}] — variables de entorno primero (instalaciones antiguas)."""
    out: list[dict[str, Any]] = []
    kinds_from_env: set[str] = set()
    for kind, (url_key, token_key) in ENV_SERVERS.items():
        url = os.environ.get(url_key, "").strip()
        token = os.environ.get(token_key, "").strip()
        if url and token:
            out.append({"kind": kind, "url": url.rstrip("/"), "token": token, "source": "env"})
            kinds_from_env.add(kind)
    for srv in load_config()["servers"]:
        if srv.get("kind") not in kinds_from_env and srv.get("url") and srv.get("token"):
            out.append({**srv, "source": "web"})
    return out


def _auth_value(token: str | None) -> str:
    device = load_config()["device_id"]
    value = f'MediaBrowser Client="Kaimaku", Device="Kaimaku", DeviceId="{device}", Version="{VERSION}"'
    if token:
        value += f', Token="{token}"'
    return value


def server_call(base: str, path: str, token: str | None = None, method: str = "GET",
                body: Any = None, timeout: float = 15) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = request.Request(base.rstrip("/") + path, data=data, method=method)
    auth = _auth_value(token)
    # Jellyfin 12 solo acepta "Authorization"; Emby usa "X-Emby-Authorization"/"X-Emby-Token".
    req.add_header("Authorization", auth)
    req.add_header("X-Emby-Authorization", auth)
    if token:
        req.add_header("X-Emby-Token", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def public_info(base: str, timeout: float = 3) -> dict[str, Any] | None:
    try:
        info = server_call(base, "/System/Info/Public", timeout=timeout)
    except Exception:
        return None
    if not isinstance(info, dict) or not (info.get("Id") or info.get("ServerName")):
        return None
    product = str(info.get("ProductName") or "")
    kind = "jellyfin" if "jellyfin" in product.lower() else "emby"
    return {
        "kind": kind,
        "url": base.rstrip("/"),
        "name": info.get("ServerName") or kind.title(),
        "version": info.get("Version"),
        "id": info.get("Id"),
    }


def _default_gateway() -> str | None:
    """IP del host Docker vista desde el contenedor (red bridge)."""
    try:
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) > 2 and fields[1] == "00000000":
                return socket.inet_ntoa(bytes.fromhex(fields[2])[::-1])
    except Exception:
        pass
    return None


def discover_servers(extra_urls: list[str] | None = None) -> list[dict[str, Any]]:
    """Prueba los sitios donde suele estar Jellyfin/Emby respecto a este contenedor."""
    hosts = ["host.docker.internal", _default_gateway(), "172.17.0.1", "jellyfin", "emby", "embyserver", "localhost"]
    urls: list[str] = list(extra_urls or [])
    for kind, (url_key, _tk) in ENV_SERVERS.items():
        if os.environ.get(url_key, "").strip():
            urls.append(os.environ[url_key].strip())
    for host in hosts:
        if not host:
            continue
        for port in (8096, 8097):
            urls.append(f"http://{host}:{port}")
    seen_urls: list[str] = []
    for u in urls:
        u = u.rstrip("/")
        if u not in seen_urls:
            seen_urls.append(u)
    with ThreadPoolExecutor(max_workers=16) as pool:
        infos = list(pool.map(lambda u: public_info(u, timeout=2.5), seen_urls))
    found: dict[str, dict[str, Any]] = {}
    for info in infos:
        if info and (info["id"] or info["url"]) not in found:
            found[info["id"] or info["url"]] = info  # mismo servidor por varias rutas: la 1a gana
    return list(found.values())


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        if exc.code == 401:
            return "Usuario o contraseña incorrectos."
        if exc.code == 403:
            return "El servidor ha rechazado el acceso (¿usuario sin permisos de administrador?)."
        return f"El servidor respondió con error {exc.code}."
    if isinstance(exc, URLError):
        return "No se puede conectar con esa dirección. Comprueba la IP y el puerto (normalmente 8096)."
    return str(exc) or exc.__class__.__name__


def login(url: str, username: str, password: str) -> dict[str, Any]:
    url = normalize_url(url)
    info = public_info(url, timeout=6)
    if not info:
        raise ValueError("En esa dirección no responde ningún Jellyfin ni Emby. Comprueba la IP y el puerto (normalmente 8096).")
    try:
        res = server_call(url, "/Users/AuthenticateByName", method="POST",
                          body={"Username": username, "Pw": password}, timeout=20)
    except Exception as exc:
        raise ValueError(friendly_error(exc)) from exc
    user = (res or {}).get("User") or {}
    if not (user.get("Policy") or {}).get("IsAdministrator"):
        raise ValueError(f"«{username}» no es administrador. Kaimaku necesita un usuario administrador para ver las carpetas de tus bibliotecas.")
    return _store_server(info, res["AccessToken"], user.get("Name") or username)


def login_with_key(url: str, api_key: str) -> dict[str, Any]:
    url = normalize_url(url)
    info = public_info(url, timeout=6)
    if not info:
        raise ValueError("En esa dirección no responde ningún Jellyfin ni Emby.")
    try:
        server_call(url, "/Library/VirtualFolders", token=api_key)
    except Exception as exc:
        raise ValueError(friendly_error(exc)) from exc
    return _store_server(info, api_key, "API key")


def _store_server(info: dict[str, Any], token: str, user: str) -> dict[str, Any]:
    cfg = load_config()
    servers = [s for s in cfg["servers"] if s.get("kind") != info["kind"]]
    entry = {"kind": info["kind"], "url": info["url"], "token": token, "user": user,
             "name": info["name"], "id": info["id"]}
    servers.append(entry)
    save_config({**cfg, "servers": servers})
    return {k: v for k, v in entry.items() if k != "token"}


def forget_server(kind: str) -> None:
    cfg = load_config()
    save_config({**cfg, "servers": [s for s in cfg["servers"] if s.get("kind") != kind]})


def normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and not re.match(r"^https?://", url, re.I):
        url = "http://" + url
    if re.match(r"^https?://[^/:]+$", url, re.I):
        url += ":8096"  # puerto por defecto de Jellyfin y Emby
    return url


def server_libraries(srv: dict[str, Any]) -> list[dict[str, Any]]:
    folders = server_call(srv["url"], "/Library/VirtualFolders", token=srv["token"], timeout=20) or []
    out = []
    for f in folders:
        out.append({
            "id": f.get("ItemId") or f.get("Id"),
            "name": f.get("Name"),
            "type": (f.get("CollectionType") or "").lower(),
            "locations": [str(loc).replace("\\", "/").rstrip("/") for loc in (f.get("Locations") or [])],
        })
    return out


def library_samples(srv: dict[str, Any], lib: dict[str, Any], limit: int = 80) -> dict[str, set[str]]:
    """location -> nombres (en minusculas) de las carpetas de primer nivel que el
    servidor conoce dentro de esa ubicacion (p. ej. 'avatar (2009)')."""
    out: dict[str, set[str]] = {loc: set() for loc in lib["locations"]}
    paths: list[str] = []
    for query in (
        f"/Items?ParentId={lib['id']}&Recursive=true&IncludeItemTypes=Movie,Series&Fields=Path&Limit={limit}",
        f"/Items?ParentId={lib['id']}&Fields=Path&Limit={limit}",
    ):
        try:
            data = server_call(srv["url"], query, token=srv["token"], timeout=30) or {}
        except Exception:
            continue
        paths = [str(i.get("Path") or "").replace("\\", "/") for i in data.get("Items") or [] if i.get("Path")]
        if paths:
            break
    for p in paths:
        for loc in lib["locations"]:
            prefix = loc.rstrip("/") + "/"
            if p.lower().startswith(prefix.lower()):
                first = p[len(prefix):].split("/")[0]
                if first:
                    out[loc].add(first.lower())
                break
    return out


# ---------------------------------------------------------------------------
# Indice de carpetas montadas (/host y /media) para localizar las bibliotecas
# ---------------------------------------------------------------------------

SKIP_DIR_NAMES = {
    "appdata", "system", "docker", "isos", "domains", "lost+found", "proc", "sys", "dev", "run", "tmp", "var",
    "usr", "lib", "lib64", "bin", "sbin", "etc", "boot", "snap", "opt", "srv-cache", "metadata", "cache",
    "transcodes", "transcode", "node_modules", "system volume information", "$recycle.bin", "recycle bin",
    "theme-music", "backdrops", "extras", "featurettes", "subs", "subtitles", "trailers",
}
# Carpetas de temporada dentro de una serie: no se baja en ellas. OJO: "series" a
# secas es un nombre de biblioteca muy comun; solo cuenta con numero ("Series 2").
LEAF_DIR_RE = re.compile(r"^((season|temporada|staffel|saison)\s*\d*|series\s*\d+|specials?|especiales)$", re.I)
YEAR_RE = re.compile(r"\((19|20)\d{2}\)")


def search_roots() -> list[Path]:
    return [p for p in (HOST_BASE, MEDIA_BASE) if p.is_dir()]


class DirIndex:
    def __init__(self) -> None:
        self.children: dict[str, set[str]] = {}  # dir -> nombres en minusculas de lo que contiene
        self.by_name: dict[str, list[str]] = {}  # basename en minusculas -> [dirs]
        self.built_at = 0.0
        self.visited = 0
        self.truncated = False


_index: DirIndex | None = None
_index_lock = threading.Lock()
INDEX_TTL = 600.0


def _list_dir(path: str) -> list[os.DirEntry[str]]:
    try:
        with os.scandir(path) as it:
            return list(it)
    except OSError:
        return []


def build_index(max_depth: int = 7, max_dirs: int = 60000, max_seconds: float = 90.0) -> DirIndex:
    idx = DirIndex()
    started = time.time()
    queue: list[tuple[str, int]] = [(str(r), 0) for r in search_roots()]
    while queue:
        path, depth = queue.pop(0)
        if idx.visited >= max_dirs or time.time() - started > max_seconds:
            idx.truncated = True
            break
        entries = _list_dir(path)
        idx.visited += 1
        names: set[str] = set()
        subdirs: list[os.DirEntry[str]] = []
        has_video = False
        for e in entries:
            names.add(e.name.lower())
            try:
                if e.is_dir(follow_symlinks=False):
                    subdirs.append(e)
                elif os.path.splitext(e.name)[1].lower() in VIDEO_EXT:
                    has_video = True
            except OSError:
                continue
        idx.children[path] = names
        idx.by_name.setdefault(os.path.basename(path).lower(), []).append(path)
        # No bajar dentro de: carpetas de una peli/serie (tienen video o Season xx),
        # bibliotecas ya reconocibles (muchos "Titulo (2010)") ni carpetas enormes.
        if has_video or depth >= max_depth:
            continue
        with_year = sum(1 for d in subdirs if YEAR_RE.search(d.name))
        library_like = len(subdirs) > 400 or (len(subdirs) >= 5 and with_year / len(subdirs) >= 0.5)
        if library_like:
            continue  # sus nombres ya estan en children[path], que es lo que se compara
        for d in subdirs:
            lname = d.name.lower()
            if lname.startswith((".", "@", "#", "$")) or lname in SKIP_DIR_NAMES or LEAF_DIR_RE.match(lname):
                continue
            queue.append((d.path, depth + 1))
    idx.built_at = time.time()
    return idx


def get_index(force: bool = False) -> DirIndex:
    global _index
    with _index_lock:
        if force or _index is None or time.time() - _index.built_at > INDEX_TTL:
            _index = build_index()
        return _index


def warm_index_async() -> None:
    threading.Thread(target=get_index, daemon=True).start()


def locate(location: str, samples: set[str]) -> dict[str, Any]:
    """Busca la carpeta montada que corresponde a una ubicacion de biblioteca del servidor."""
    idx = get_index()
    base = os.path.basename(location.rstrip("/")).lower()

    def score(d: str) -> tuple[float, int, int]:
        names = idx.children.get(d, set())
        # 3er criterio: a igualdad, la carpeta con mas contenido. En Unraid con /mnt
        # montado entero la misma biblioteca existe en /mnt/user (completa) y en cada
        # /mnt/diskN (solo una parte): debe ganar /mnt/user.
        if not samples:
            return 0.0, 0, len(names)
        hit = len(samples & names)
        return hit / len(samples), hit, len(names)

    best: tuple[float, int, int, str] | None = None
    for d in idx.by_name.get(base, []):
        if d not in idx.children:
            continue
        sc_ = score(d)
        if best is None or sc_ > best[:3]:
            best = (*sc_, d)
    if samples and (best is None or best[0] < 0.5):
        # El nombre no coincide (p. ej. el servidor la ve como /movies y aqui es
        # /host/pelis): se busca por contenido en todo el indice.
        for d, names in idx.children.items():
            if len(names) < 2:
                continue
            sc_ = score(d)
            if sc_[0] > 0 and (best is None or sc_ > best[:3]):
                best = (*sc_, d)
    if best is None:
        return {"path": None, "confidence": 0.0, "matched": 0, "total": len(samples)}
    s, hit, _size, d = best
    if samples and s < 0.3:
        return {"path": None, "confidence": round(s, 2), "matched": hit, "total": len(samples), "guess": d}
    return {"path": d, "confidence": round(s, 2) if samples else 0.5, "matched": hit, "total": len(samples)}


def map_libraries() -> dict[str, Any]:
    """Para cada biblioteca de cada servidor conectado: donde esta dentro de /host."""
    servers = configured_servers()
    result: list[dict[str, Any]] = []
    errors: list[str] = []
    merged: dict[str, dict[str, Any]] = {}  # path montado -> entrada (Jellyfin y Emby comparten carpetas)
    for srv in servers:
        try:
            libs = server_libraries(srv)
        except Exception as exc:
            errors.append(f"{srv['kind'].title()}: {friendly_error(exc)}")
            continue
        for lib in libs:
            if lib["type"] not in VIDEO_COLLECTION_TYPES:
                continue
            samples = library_samples(srv, lib)
            for loc in lib["locations"]:
                found = locate(loc, samples.get(loc, set()))
                key = found["path"] or f"?{srv['kind']}:{lib['id']}:{loc}"
                entry = merged.get(key)
                if not entry:
                    entry = {
                        "path": found["path"],
                        "label": lib["name"],
                        "type": lib["type"],
                        "confidence": found["confidence"],
                        "matched": found["matched"],
                        "total": found["total"],
                        "guess": found.get("guess"),
                        "servers": {},
                        "enabled": bool(found["path"]),
                    }
                    merged[key] = entry
                entry["servers"][srv["kind"]] = {"id": lib["id"], "location": loc, "library": lib["name"]}
    result = sorted(merged.values(), key=lambda e: (e["path"] is None, e["label"].lower()))
    idx = get_index()
    return {"libraries": result, "errors": errors, "index": {"dirs": idx.visited, "truncated": idx.truncated}}


# ---------------------------------------------------------------------------
# Explorador de carpetas (para elegir bibliotecas a mano)
# ---------------------------------------------------------------------------

def allowed_path(path: str) -> Path | None:
    try:
        p = Path(path).resolve()
    except Exception:
        return None
    for root in search_roots():
        try:
            p.relative_to(root.resolve())
            return p
        except ValueError:
            continue
    return None


def browse(path: str | None) -> dict[str, Any]:
    roots = search_roots()
    if not path:
        return {"path": None, "parent": None, "dirs": [{"name": str(r), "path": str(r)} for r in roots]}
    p = allowed_path(path)
    if not p or not p.is_dir():
        raise ValueError("Esa carpeta no está dentro de lo que has montado en Kaimaku.")
    dirs = []
    for e in sorted(_list_dir(str(p)), key=lambda e: e.name.lower()):
        try:
            if e.is_dir() and not e.name.startswith("."):
                dirs.append({"name": e.name, "path": e.path})
        except OSError:
            continue
    parent = str(p.parent) if any(p != r.resolve() for r in roots) and allowed_path(str(p.parent)) else None
    sample = [d["name"] for d in dirs[:6]]
    return {"path": str(p), "parent": parent, "dirs": dirs[:500], "sample": sample, "count": len(dirs)}


# ---------------------------------------------------------------------------
# Dueño de los archivos (sustituye a PUID/PGID)
# ---------------------------------------------------------------------------

def owner_of(paths: list[str]) -> tuple[int, int] | None:
    """Usuario:grupo mas comun entre las carpetas de las bibliotecas (sin contar root)."""
    counts: Counter[tuple[int, int]] = Counter()
    for root in paths:
        for e in _list_dir(root)[:40]:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            counts[(st.st_uid, st.st_gid)] += 1
    non_root = [(k, v) for k, v in counts.most_common() if k[0] != 0]
    if non_root:
        return non_root[0][0]
    return counts.most_common(1)[0][0] if counts else None


def mount_status() -> dict[str, Any]:
    out = {}
    for name, p in (("host", HOST_BASE), ("media", MEDIA_BASE)):
        exists = p.is_dir()
        entries = [e.name for e in _list_dir(str(p))[:12]] if exists else []
        out[name] = {"path": str(p), "mounted": exists and bool(entries), "sample": entries}
    return out
