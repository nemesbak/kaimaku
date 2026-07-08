# 開幕 Kaimaku

*Kaimaku* (開幕, "se alza el telón") busca, puntúa e instala openings/temas para tus bibliotecas de Jellyfin y Emby — como `theme-music/song1.mp3` y `backdrops/intro.mp4` — priorizando fuentes oficiales en español/castellano/latino cuando existen.

Incluye dos formas de usarlo:

- **[`docker-app/`](docker-app)** — interfaz web (recomendada). Modo manual (revisas cada candidato) o autopiloto (biblioteca entera sin más clics, con umbral de confianza), cola en tiempo real, cancelar/reintentar, todas tus bibliotecas (no solo anime).
- **`anime_theme_sync.py`** — CLI por lotes para procesar una biblioteca entera de una vez (scan → search → stage → install → refresh), pensada para automatizar vía cron/user scripts.

Ambos comparten la misma lógica de puntuación: penaliza covers/AMV/reactions/reviews y prioriza openings oficiales en español/castellano.

## Interfaz web (Kaimaku)

```bash
git clone https://github.com/nemesbak/kaimaku.git
cd kaimaku/docker-app
cp .env.example .env   # rellena MEDIA_HOST_PATH, MEDIA_ROOTS y, opcionalmente, las API keys
docker compose up -d --build
```

Abre `http://SERVER-IP:8098`. Guía completa (variables, arquitectura, solución de problemas) en [`docker-app/README.md`](docker-app/README.md).

## CLI por lotes

Requisitos: Python 3.10+, [`yt-dlp`](https://github.com/yt-dlp/yt-dlp), `ffmpeg` (si vas a convertir audio/video).

```bash
pip install yt-dlp
python anime_theme_sync.py init          # crea config.json desde config.example.json
```

Rellena las API keys de Jellyfin/Emby en `config.json` (o usa `filesystem_roots` para escanear carpetas sin API), y ejecuta el flujo:

```bash
python anime_theme_sync.py scan                                          # 1. lista la biblioteca
python anime_theme_sync.py search --limit 10                             # 2. busca candidatos en YouTube (no descarga)
python anime_theme_sync.py report --input state/anime_candidates.json    # 3. revisa el resumen en Markdown
python anime_theme_sync.py stage  --input state/anime_candidates.json --output staged   # 4. descarga a una carpeta local
python install_staged_remote.py --stage-root staged --dry-run            # 5. revisa qué se instalaría...
python install_staged_remote.py --stage-root staged --backup-existing    # ...e instálalo
```

`anime_theme_sync.py refresh` refresca Jellyfin/Emby a partir del listado de cambios que genera el comando `download` (`--changed`); el flujo `stage` de arriba no lo produce, así que tras instalar refresca las bibliotecas afectadas a mano (o usa la interfaz web, que sí refresca automáticamente cada instalación).

Genera, por cada serie o película:

```text
Serie (Año)
├── theme-music
│   └── song1.mp3
└── backdrops
    └── intro.mp4
```

## Cómo puntúa los candidatos

- `+` título oficial / canal conocido (Crunchyroll, Aniplex, Toho...)
- `+` contiene "opening"/"OP"/"theme"
- `+` español, castellano, latino
- `+` duración corta (opening real, no el episodio completo)
- `−` reaction, cover, piano, AMV, nightcore, review

## Notas

- Ambas herramientas son deliberadamente prudentes: buscan y muestran candidatos antes de descargar nada; la instalación final siempre es un paso explícito.
- Descarga contenido de YouTube para uso personal en tu propio servidor de medios — respeta los términos de uso de YouTube y los derechos del contenido que descargues.
- `staged/`, `state/` y los `*.tar.gz` son artefactos locales (resultados de escaneo, descargas) — no se versionan.

## Licencia

[MIT](LICENSE)
