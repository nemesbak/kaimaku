# 開幕 Kaimaku

**Kaimaku** (開幕, "se alza el telón") es una app web para Jellyfin y Emby que busca, puntúa e instala automáticamente los openings/temas de tus series y películas — genera `theme-music/song1.mp3` y `backdrops/intro.mp4` en cada carpeta, priorizando fuentes oficiales y en español/castellano/latino cuando existen.

![status](https://img.shields.io/badge/estado-uso%20personal-blue)

```text
Serie o Película
├── theme-music
│   └── song1.mp3
└── backdrops
    └── intro.mp4
```

## Instalación

Requisito único: Docker + Docker Compose v2 (`docker compose`, no `docker-compose`). Si no lo tienes, mira [cómo instalarlo](docker-app/README.md#instalar-docker-si-no-lo-tienes-ya).

```bash
git clone https://github.com/nemesbak/kaimaku.git
cd kaimaku/docker-app
cp .env.example .env
```

Abre `.env` y cambia como mínimo `MEDIA_HOST_PATH` por la ruta real de tu biblioteca. El resto de valores por defecto funcionan tal cual.

```bash
docker compose up -d --build
```

Abre `http://IP-DEL-SERVIDOR:8098` y ya está.

Guía completa (todas las variables de `.env`, arquitectura, solución de problemas) en **[`docker-app/README.md`](docker-app/README.md)**.

## Funciones

- Ve todas las bibliotecas configuradas (anime, series, películas...), con filtro "sin tema / con tema".
- **Modo manual**: eliges destino, la búsqueda automática construye varias consultas en YouTube, puntúa los resultados y preselecciona el mejor candidato para que lo revises antes de instalar.
- **Modo autopiloto**: eliges una biblioteca completa (o un destino) y un umbral mínimo de confianza — Kaimaku busca, puntúa e instala cada ítem automáticamente solo si supera el umbral.
- Cola de instalación en tiempo real, cancelar/reintentar en cualquier momento.
- Backup automático del archivo existente antes de sobrescribirlo.
- Refresca solo la biblioteca de Jellyfin/Emby afectada tras instalar (si configuras las API keys).

## Cómo puntúa los candidatos

- `+` título oficial / canal conocido (Crunchyroll, Aniplex, Toho...)
- `+` contiene "opening"/"OP"/"theme"
- `+` español, castellano, latino
- `+` duración corta (opening real, no el episodio completo)
- `−` reaction, cover, piano, AMV, nightcore, review

## Notas

- Kaimaku es deliberadamente prudente: busca y muestra candidatos antes de descargar nada; la instalación final siempre es un paso explícito que tú confirmas (o un umbral de confianza que tú eliges en autopiloto).
- Descarga contenido de YouTube para uso personal en tu propio servidor de medios — respeta los términos de uso de YouTube y los derechos del contenido que descargues.

## Licencia

[MIT](LICENSE)
