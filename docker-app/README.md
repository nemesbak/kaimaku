# 開幕 Kaimaku

*Kaimaku* (開幕) es japonés para "se alza el telón". Es la interfaz web para instalar `theme-music/song1.mp3` y `backdrops/intro.mp4` (openings/temas) en tus bibliotecas de Jellyfin/Emby.

![status](https://img.shields.io/badge/estado-uso%20personal-blue)

## Funciones

- Ve todas las bibliotecas configuradas en `MEDIA_ROOTS` (anime, series, películas...), con pestañas para filtrar y un filtro rápido "sin tema / con tema".
- **Modo manual**: eliges destino, la búsqueda automática construye varias consultas en YouTube (`opening español castellano`, `opening oficial`, etc.), puntúa los resultados priorizando fuentes oficiales y español/castellano/latino, y preselecciona el mejor candidato para que lo revises antes de instalar. Búsqueda manual y pegar un enlace directo como alternativas.
- **Modo autopiloto**: elige una biblioteca completa o un único destino, un umbral mínimo de confianza y qué instalar — y lo procesa entero sin más clics. Cada ítem se busca, se puntúa y se instala automáticamente solo si supera el umbral; si no, se omite (nunca instala algo dudoso). Progreso en vivo y botón de detener en cualquier momento.
- Cola de instalación en tiempo real con logs por trabajo, cancelar jobs en curso/en cola y reintentar los fallidos.
- Backup automático del archivo existente antes de sobrescribirlo (el autopiloto no sobrescribe destinos ya instalados salvo que actives "Sobrescribir existentes").
- Refresca solo la biblioteca de Jellyfin/Emby afectada tras instalar (no un escaneo completo del servidor), si configuras las API keys.

## Requisitos

- Docker + Docker Compose v2 (`docker compose`, no `docker-compose`).
- Una carpeta con tu biblioteca de medios accesible desde el host donde corre Docker.
- Opcional: Jellyfin y/o Emby, con una API key cada uno, para el refresco automático.

No hace falta Python, `yt-dlp` ni `ffmpeg` en el host — todo vive dentro de la imagen.

## Instalación paso a paso

```bash
git clone https://github.com/nemesbak/kaimaku.git
cd kaimaku/docker-app
cp .env.example .env
```

Edita `.env`:

1. `MEDIA_HOST_PATH` → la carpeta real de tu biblioteca en el host (ej. `/mnt/user/datos/media` en Unraid, o `/srv/media` en un servidor genérico).
2. `DATA_HOST_PATH` → una carpeta pequeña y persistente para el estado de Kaimaku (backups). Puede vivir dentro o fuera de `MEDIA_HOST_PATH`, no importa.
3. `MEDIA_ROOTS` → qué subcarpetas de esa biblioteca quieres que aparezcan como "bibliotecas" en la interfaz, **tal como se llaman dentro del contenedor** (`/media/<nombre>`, no la ruta del host). Puedes poner tantas como quieras separadas por comas.
4. `JELLYFIN_URL` / `JELLYFIN_API_KEY` y `EMBY_URL` / `EMBY_API_KEY` → opcional. Sin API key la instalación funciona igual, solo se omite el refresco automático de biblioteca.

```bash
docker compose up -d --build
```

Abre `http://IP-DEL-SERVIDOR:8098` (o el puerto que hayas puesto en `PORT`).

### Actualizar a una versión nueva

```bash
git pull
docker compose up -d --build
```

Si hay un trabajo o una ejecución de autopiloto en curso en ese momento, Kaimaku espera (hasta 25s) a que termine antes de apagarse — no interrumpe una descarga a medias. Ver [Arquitectura](#arquitectura-y-comportamiento) más abajo.

### Desinstalar

```bash
docker compose down
```

Tu biblioteca de medios no se toca. Solo se borra el contenedor; `DATA_HOST_PATH` (backups) y las imágenes construidas quedan en el host hasta que las borres tú a mano.

## Variables (`.env`)

| Variable | Descripción |
| --- | --- |
| `MEDIA_HOST_PATH` | Ruta real de tu biblioteca en el host. Se monta como `/media` dentro del contenedor. |
| `DATA_HOST_PATH` | Ruta en el host para backups y archivos temporales de descarga. Pequeña, no necesita estar en el mismo disco que la biblioteca. |
| `CONTAINER_NAME` | Nombre del contenedor Docker. Por defecto `kaimaku`. |
| `PORT` | Puerto publicado en el host. Por defecto `8098`. |
| `MEDIA_ROOTS` | Bibliotecas a mostrar, separadas por comas, **relativas a `/media` dentro del contenedor** (ej. `/media/anime,/media/series`), no a `MEDIA_HOST_PATH`. |
| `JELLYFIN_URL` / `JELLYFIN_API_KEY` | Opcional. Sin API key, la instalación funciona igual pero se omite el refresco de biblioteca. |
| `EMBY_URL` / `EMBY_API_KEY` | Igual que arriba, para Emby. |

`host.docker.internal` (usado por defecto en `JELLYFIN_URL`/`EMBY_URL`) apunta al propio host Docker — sirve si Jellyfin/Emby corren como contenedores normales o directamente en el host. Si en cambio quieres que Kaimaku les hable por nombre de contenedor (misma red Docker), añade Jellyfin/Emby como `services:` en este mismo `docker-compose.yml` o conecta este servicio a su red con `networks:`.

## Estructura generada

```text
Serie o Película
├── theme-music
│   └── song1.mp3
└── backdrops
    └── intro.mp4
```

Los backups del archivo previo (si lo había) quedan en `DATA_HOST_PATH/backups/<fecha>/...` antes de sobrescribir.

## Arquitectura y comportamiento

Pensado para ser simple de operar en un homelab, con algunas decisiones de diseño explícitas:

- **Estado en memoria, no en base de datos.** Los trabajos y ejecuciones de autopiloto viven en memoria del proceso — nada que corromper, nada que migrar. La contrapartida: un reinicio del contenedor los pierde. Por eso el apagado espera a que termine el trabajo en curso (ver abajo) en vez de simplemente matarlo.
- **Apagado ordenado.** Al recibir `SIGTERM` (`docker compose down`, `docker compose up --build`, `docker stop`...), si hay un trabajo descargando, Kaimaku espera hasta 25s a que termine antes de salir. `stop_grace_period: 30s` en el compose asegura que Docker no lo mate con `SIGKILL` antes de que le dé tiempo.
- **Limpieza automática.** La carpeta de trabajo temporal (`DATA_HOST_PATH/work/<job-id>`) se borra al terminar cada trabajo (éxito, fallo o cancelación), y también al arrancar el contenedor por si quedó algo de un cierre no ordenado (caída, `kill -9`...).
- **Refresco de biblioteca acotado.** Tras instalar, refresca solo la biblioteca de Jellyfin/Emby que contiene el destino afectado (por nombre de carpeta), no un escaneo completo del servidor — importante si tienes muchas bibliotecas y/o usuarios.
- **Logs del contenedor con límite.** `docker-compose.yml` cap a 10MB × 3 archivos (`json-file`, `max-size`/`max-file`) para que no crezcan sin límite con el uso normal.

## Solución de problemas

**Las descargas fallan con `HTTP Error 403: Forbidden` o menciones a "JavaScript runtime"/"EJS"**
YouTube exige ejecutar JavaScript para resolver el cifrado de sus URLs de vídeo. La imagen ya incluye [Deno](https://deno.com/) (runtime) y el paquete `yt-dlp-ejs` (script solucionador) para esto — si ves este error en una imagen construida desde este repo, comprueba que estás en la última versión (`git pull && docker compose up -d --build`). Si persiste para un vídeo concreto, prueba con otro: YouTube a veces limita temporalmente un vídeo específico sin que sea un problema del contenedor.

**Un trabajo falla con `This video is not available` / `Video unavailable`**
El vídeo elegido por la búsqueda ya no existe o se volvió privado entre que se encontró y se descargó. No es un fallo de Kaimaku — usa "Reintentar" (relanza la búsqueda) o pega un enlace manualmente.

**El autopiloto omite casi todo en una biblioteca que no es de anime**
Si el nombre de la carpeta de biblioteca contiene "anime" o "animacion", Kaimaku usa plantillas de búsqueda orientadas a openings de anime; para el resto usa plantillas genéricas (tema principal, banda sonora, tráiler oficial). Aun así, algunas bibliotecas (documentales, true crime...) no siempre tienen un "tema" real que encontrar — el umbral de confianza está para evitar instalar algo incorrecto antes que forzar un resultado dudoso.

**No refresca Jellyfin/Emby tras instalar**
Comprueba que `JELLYFIN_API_KEY`/`EMBY_API_KEY` están rellenas en `.env` y que `JELLYFIN_URL`/`EMBY_URL` son alcanzables **desde dentro del contenedor** (no desde tu navegador) — prueba `docker compose exec kaimaku python -c "import urllib.request as u; print(u.urlopen('URL/System/Info').status)"` con la API key como header `X-Emby-Token` si tienes dudas.

## Desarrollo local (sin Docker)

```bash
cd docker-app
pip install -r requirements.txt
MEDIA_ROOTS=/ruta/a/tu/biblioteca DATA_DIR=./data uvicorn app.main:app --reload --port 8098
```

Sin Deno/`yt-dlp-ejs` instalados en el sistema, las búsquedas y descargas de YouTube pueden fallar con los mismos errores descritos arriba — instala [Deno](https://docs.deno.com/runtime/getting_started/installation/) y `pip install yt-dlp-ejs` si vas a probar esto fuera de Docker.
