# 開幕 Kaimaku

Instala automáticamente los openings/temas (`theme-music/song1.mp3` y `backdrops/intro.mp4`) de tus series y películas en Jellyfin/Emby, buscándolos en YouTube y priorizando fuentes oficiales en español/castellano/latino.

![status](https://img.shields.io/badge/estado-uso%20personal-blue)

## Instalar

Requisito único: Docker + Docker Compose v2 (`docker compose`, no `docker-compose`).

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
    build: .
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
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

Lo único que **tienes** que cambiar es la línea marcada `<-- CAMBIA ESTA`, por la ruta real de tu biblioteca. `MEDIA_ROOTS`, el puerto y Jellyfin/Emby ya vienen con valores que funcionan tal cual (ajústalos solo si quieres refresco automático o rutas distintas).

**3. Levanta el contenedor**

```bash
docker compose up -d --build
```

**4. Abre la app**

```text
http://IP-DEL-SERVIDOR:8098
```

## Actualizar

```bash
git pull
docker compose up -d --build
```

## Desinstalar

```bash
docker compose down
```

Tu biblioteca de medios no se toca; solo se borra el contenedor.

## Solución de problemas

**Las descargas fallan con `HTTP Error 403: Forbidden` o mencionan "JavaScript runtime"/"EJS"**
YouTube exige ejecutar JavaScript para resolver el cifrado de sus URLs de vídeo. La imagen ya trae lo necesario para esto — comprueba que estás en la última versión (`git pull && docker compose up -d --build`).

**No refresca Jellyfin/Emby tras instalar**
Comprueba que `JELLYFIN_API_KEY`/`EMBY_API_KEY` están rellenas en `docker-compose.yml` y que `JELLYFIN_URL`/`EMBY_URL` son alcanzables desde el contenedor, no solo desde tu navegador.

## Licencia

[MIT](LICENSE)
