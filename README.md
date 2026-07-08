# 開幕 Kaimaku

Instala automáticamente los openings/temas (`theme-music/song1.mp3` y `backdrops/intro.mp4`) de tus series y películas en Jellyfin/Emby, buscándolos en YouTube y priorizando fuentes oficiales en español/castellano/latino.

[![Docker Pulls](https://img.shields.io/docker/pulls/nemesbak/kaimaku?logo=docker&label=pulls)](https://hub.docker.com/r/nemesbak/kaimaku)
[![Image size](https://img.shields.io/docker/image-size/nemesbak/kaimaku/latest?logo=docker&label=tama%C3%B1o)](https://hub.docker.com/r/nemesbak/kaimaku)
![arch](https://img.shields.io/badge/arquitectura-amd64%20%7C%20arm64-informational?logo=docker)
[![License: MIT](https://img.shields.io/badge/licencia-MIT-green)](LICENSE)
![status](https://img.shields.io/badge/estado-uso%20personal-blue)

## Instalar

Requisito único: Docker + Docker Compose v2 (`docker compose`, no `docker-compose`). La imagen ya está publicada en [Docker Hub](https://hub.docker.com/r/nemesbak/kaimaku) para `amd64` y `arm64` (funciona también en Raspberry Pi, Synology, etc.) — no hace falta compilar nada.

**1. Clona el repositorio**

```bash
git clone https://github.com/nemesbak/kaimaku.git
cd kaimaku/docker-app
```

**2. Edita `docker-app/docker-compose.yml`**

Este es el archivo completo, tal cual viene en el repo — no hay ningún `.env` aparte, todo está aquí:

```yaml
services:
  kaimaku:
    image: nemesbak/kaimaku:latest
    container_name: kaimaku
    ports:
      - "8098:8098"   # <-- puerto donde abrirás Kaimaku: http://IP-DEL-SERVIDOR:8098
    environment:
      # Bibliotecas a mostrar, separadas por comas. Rutas DENTRO del contenedor
      # (relativas al /media de "volumes" de abajo, no a tu ruta real del host).
      MEDIA_ROOTS: "/media/anime,/media/series,/media/peliculas"
      DATA_DIR: "/data"
      # Jellyfin/Emby son opcionales: sin API key, todo funciona igual pero se
      # omite el refresco automático de biblioteca tras instalar.
      # host.docker.internal apunta al propio host Docker (sirve si Jellyfin/Emby
      # corren en el host o como contenedores normales). API key: Panel de
      # control → API Keys → Nueva clave.
      JELLYFIN_URL: "http://host.docker.internal:8096"
      JELLYFIN_API_KEY: ""
      EMBY_URL: "http://host.docker.internal:8097"
      EMBY_API_KEY: ""
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      # <-- CAMBIA ESTA: la ruta REAL de tu biblioteca en el host (donde están
      # tus carpetas anime/, series/, peliculas/...). Se monta como /media.
      - /mnt/user/datos/media:/media
      # Carpeta pequeña para backups y descargas en curso. No hace falta tocarla.
      - ./data:/data
    restart: unless-stopped
    # Job/autopilot state lives in memory only (simple, nothing to corrupt) —
    # on shutdown the app waits up to 25s for an in-progress download to finish
    # before exiting. Keep this above that so it isn't SIGKILLed mid-wait.
    stop_grace_period: 30s
```

Lo único que **tienes** que cambiar es la línea marcada `<-- CAMBIA ESTA`, por la ruta real de tu biblioteca. `MEDIA_ROOTS`, el puerto y Jellyfin/Emby ya vienen con valores que funcionan tal cual (ajústalos solo si quieres refresco automático o rutas distintas).

**3. Levanta el contenedor**

```bash
docker compose up -d
```

**4. Abre la app**

```text
http://IP-DEL-SERVIDOR:8098
```

## Actualizar

```bash
docker compose pull
docker compose up -d
```

## Desinstalar

```bash
docker compose down
```

Tu biblioteca de medios no se toca; solo se borra el contenedor.

## Solución de problemas

**Las descargas fallan con `HTTP Error 403: Forbidden` o mencionan "JavaScript runtime"/"EJS"**
YouTube exige ejecutar JavaScript para resolver el cifrado de sus URLs de vídeo. La imagen ya trae lo necesario para esto — comprueba que estás en la última versión (`docker compose pull && docker compose up -d`).

**No refresca Jellyfin/Emby tras instalar**
Comprueba que `JELLYFIN_API_KEY`/`EMBY_API_KEY` están rellenas en `docker-compose.yml` y que `JELLYFIN_URL`/`EMBY_URL` son alcanzables desde el contenedor, no solo desde tu navegador.

**Los logs del contenedor crecen sin límite**
El `docker-compose.yml` no fija rotación de logs a propósito, para no meter ruido en un archivo pensado para ser simple. Si te importa (uso 24/7 a largo plazo), configúralo una vez para todos tus contenedores en `/etc/docker/daemon.json` en vez de por servicio:

```json
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
```

Reinicia Docker (`sudo systemctl restart docker`) tras guardarlo.

## Uso avanzado: CLI por lotes

Además de la app web, el repo incluye `kaimaku_cli.py`: un CLI para procesar una biblioteca entera de una vez (scan → search → stage → install → refresh), pensado para automatizar vía cron/user scripts en vez de usar la interfaz web. Comparte la misma lógica de puntuación que la app web.

Requisitos (a diferencia de la app web, aquí sí hacen falta en el host): Python 3.10+, [`yt-dlp`](https://github.com/yt-dlp/yt-dlp), `ffmpeg` (si vas a convertir audio/vídeo).

```bash
pip install yt-dlp
python kaimaku_cli.py init          # crea config.json desde config.example.json
```

Rellena las API keys de Jellyfin/Emby en `config.json` (o usa `filesystem_roots` para escanear carpetas sin API). Flujo completo:

```bash
python kaimaku_cli.py scan                                                  # 1. lista la biblioteca
python kaimaku_cli.py search --limit 10                                     # 2. busca candidatos en YouTube (no descarga)
python kaimaku_cli.py report --input state/candidates.json                  # 3. revisa el resumen en Markdown
python kaimaku_cli.py stage  --input state/candidates.json --output staged  # 4. descarga a una carpeta local
python install_staged_remote.py --stage-root staged --dry-run               # 5. revisa qué se instalaría...
python install_staged_remote.py --stage-root staged --backup-existing       # ...e instálalo
```

`kaimaku_cli.py refresh` refresca Jellyfin/Emby a partir del listado de cambios que genera `download --changed`; el flujo `stage` de arriba no lo produce, así que tras instalar refresca las bibliotecas afectadas a mano (o usa la interfaz web, que sí refresca automáticamente cada instalación).

## Licencia

[MIT](LICENSE)
