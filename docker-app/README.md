# 開幕 Kaimaku

*Kaimaku* (開幕) es japonés para "se alza el telón". Es la interfaz web para instalar `theme-music/song1.mp3` y `backdrops/intro.mp4` (openings/temas) en tus bibliotecas de Jellyfin/Emby.

![status](https://img.shields.io/badge/estado-uso%20personal-blue)

## Funciones

- Ve todas las bibliotecas configuradas en `MEDIA_ROOTS` (anime, series, películas...), con pestañas para filtrar.
- **Búsqueda automática**: para la serie/película elegida, construye varias búsquedas en YouTube (`opening español castellano`, `opening oficial`, etc.), puntúa los resultados priorizando fuentes oficiales y español/castellano/latino, y preselecciona el mejor candidato.
- Búsqueda manual y pegar un enlace directo como alternativas, si la automática no encuentra lo que buscas.
- Cola de instalación en tiempo real con logs por trabajo, cancelar jobs en curso/en cola y reintentar los fallidos.
- Backup automático del archivo existente antes de sobrescribirlo.
- Refresca las bibliotecas de Jellyfin/Emby tras instalar, si configuras las API keys.

## Puesta en marcha

```bash
cd docker-app
cp .env.example .env
# edita .env: MEDIA_ROOTS, JELLYFIN_URL/API_KEY, EMBY_URL/API_KEY
docker compose up -d --build
```

Abrir `http://SERVER-IP:8098`.

## Variables (`.env`)

| Variable | Descripción |
| --- | --- |
| `MEDIA_ROOTS` | Rutas de bibliotecas separadas por comas, tal como se ven **dentro** del contenedor (relativas al volumen `/media`). |
| `JELLYFIN_URL` / `JELLYFIN_API_KEY` | Opcional. Sin API key, la instalación funciona igual pero se omite el refresco de biblioteca. |
| `EMBY_URL` / `EMBY_API_KEY` | Igual que arriba, para Emby. |

`docker-compose.yml` monta `/mnt/user/datos/media` (ajusta esa ruta a la tuya) en `/media` dentro del contenedor — cambia el volumen si tu servidor usa otra ruta.

## Estructura generada

```text
Serie o Película
├── theme-music
│   └── song1.mp3
└── backdrops
    └── intro.mp4
```

Los backups quedan en `/data/backups` (mapeado por el volumen `DATA_DIR`).
