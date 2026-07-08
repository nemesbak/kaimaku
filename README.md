# 開幕 Kaimaku

**Kaimaku** (開幕, "se alza el telón") busca, puntúa e instala openings/temas para tus bibliotecas de Jellyfin y Emby: genera automáticamente `theme-music/song1.mp3` y `backdrops/intro.mp4` para cada serie o película, priorizando fuentes oficiales y en español/castellano/latino cuando existen.

![status](https://img.shields.io/badge/estado-uso%20personal-blue)

```text
Serie o Película
├── theme-music
│   └── song1.mp3
└── backdrops
    └── intro.mp4
```

## Dos formas de usarlo

| | [`docker-app/`](docker-app) (recomendado) | `kaimaku_cli.py` |
| --- | --- | --- |
| Qué es | Interfaz web | CLI por lotes |
| Uso típico | Uso interactivo día a día | Automatizar vía cron / user scripts |
| Modo manual | Sí, revisas cada candidato antes de instalar | Sí (`report` → revisas → `stage`/instalar) |
| Modo autopiloto | Sí, biblioteca entera con umbral de confianza | No (procesa la biblioteca entera igualmente, pero sin cola/UI) |
| Cola en tiempo real, cancelar/reintentar | Sí | No |
| Requiere Python/yt-dlp/ffmpeg en el host | No (todo vive en la imagen) | Sí |

Ambos comparten la misma lógica de puntuación (ver [más abajo](#cómo-puntúa-los-candidatos)).

## Instalación rápida — interfaz web

```bash
git clone https://github.com/nemesbak/kaimaku.git
cd kaimaku/docker-app
cp .env.example .env   # rellena MEDIA_HOST_PATH, MEDIA_ROOTS y, opcionalmente, las API keys
docker compose up -d --build
```

Abre `http://IP-DEL-SERVIDOR:8098`.

Guía completa (variables de `.env`, arquitectura, solución de problemas) en **[`docker-app/README.md`](docker-app/README.md)**.

## Instalación — CLI por lotes

Requisitos: Python 3.10+, [`yt-dlp`](https://github.com/yt-dlp/yt-dlp), `ffmpeg` (si vas a convertir audio/vídeo).

```bash
pip install yt-dlp
python kaimaku_cli.py init          # crea config.json desde config.example.json
```

Rellena las API keys de Jellyfin/Emby en `config.json` (o usa `filesystem_roots` para escanear carpetas sin API). Flujo completo:

```bash
python kaimaku_cli.py scan                                                # 1. lista la biblioteca
python kaimaku_cli.py search --limit 10                                   # 2. busca candidatos en YouTube (no descarga)
python kaimaku_cli.py report --input state/candidates.json                # 3. revisa el resumen en Markdown
python kaimaku_cli.py stage  --input state/candidates.json --output staged  # 4. descarga a una carpeta local
python install_staged_remote.py --stage-root staged --dry-run             # 5. revisa qué se instalaría...
python install_staged_remote.py --stage-root staged --backup-existing     # ...e instálalo
```

> `kaimaku_cli.py refresh` refresca Jellyfin/Emby a partir del listado de cambios que genera `download --changed`; el flujo `stage` de arriba no lo produce, así que tras instalar refresca las bibliotecas afectadas a mano (o usa la interfaz web, que sí refresca automáticamente cada instalación).

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
