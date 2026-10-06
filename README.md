# 開幕 Kaimaku

Instala automáticamente los openings/temas (`theme-music/song1.mp3` y `backdrops/intro.mp4`) de tus series y películas en Jellyfin/Emby, buscándolos en YouTube y priorizando fuentes oficiales en castellano. Corre en segundo plano: escanea tu biblioteca cada pocas horas, instala sola lo que encuentra con confianza suficiente, y te deja el resto marcado como "revisión" en un panel con pósters reales.

[![Docker Pulls](https://img.shields.io/docker/pulls/nemesbak/kaimaku?logo=docker&label=pulls)](https://hub.docker.com/r/nemesbak/kaimaku)
[![Image size](https://img.shields.io/docker/image-size/nemesbak/kaimaku/latest?logo=docker&label=tama%C3%B1o)](https://hub.docker.com/r/nemesbak/kaimaku)
![arch](https://img.shields.io/badge/arquitectura-amd64%20%7C%20arm64-informational?logo=docker)
[![License: MIT](https://img.shields.io/badge/licencia-MIT-green)](LICENSE)

Elige cómo instalarlo: [Docker Compose](#instalar) (cualquier sistema), [Portainer](#-portainer) o [Unraid](#️-unraid).

![Captura de Kaimaku](docs/screenshot.png)

## Instalar

No hay que saber rutas internas, API keys ni usuarios de Linux: todo eso lo resuelve un asistente en la propia web. Solo necesitas Docker.

**1 — Crea una carpeta `kaimaku` y dentro un archivo `docker-compose.yml` con esto:**

```yaml
services:
  kaimaku:
    image: nemesbak/kaimaku:latest
    container_name: kaimaku
    ports:
      - "8098:8098"   # abre Kaimaku en http://IP-DE-TU-SERVIDOR:8098
    volumes:
      # La carpeta GRANDE donde guardas tus series y películas. No hace falta
      # afinar: Kaimaku encuentra solo tus bibliotecas dentro. Cambia solo la
      # parte de la IZQUIERDA de los dos puntos según tu sistema:
      #   Unraid:        /mnt/user
      #   Synology:      /volume1
      #   TrueNAS / OMV: /mnt
      #   Linux:         /home  (o la carpeta donde tengas tus medios)
      #   Windows:       D:/    (la unidad de tus medios, con Docker Desktop)
      - /mnt/user:/host
      # Configuración y estado de Kaimaku. No hace falta tocarla.
      - ./data:/data
    extra_hosts:
      - "host.docker.internal:host-gateway"   # para encontrar tu Jellyfin/Emby en este mismo equipo
    restart: unless-stopped
    stop_grace_period: 30s   # deja terminar una descarga en curso al parar
```

Lo único que puede que tengas que cambiar es `/mnt/user` por la carpeta de tu sistema (ver la tabla de los comentarios). Sirve una carpeta "grande": no tiene que ser exactamente la de tus películas.

**2 — Arráncalo** desde esa carpeta:

```bash
docker compose up -d
```

**3 — Abre `http://IP-DE-TU-SERVIDOR:8098`** y sigue el asistente:

1. **Servidor** — Kaimaku busca solo tu Jellyfin/Emby. Escribe tu usuario y contraseña de **administrador** (la contraseña no se guarda: solo se usa para obtener un acceso propio para Kaimaku). Si tienes los dos, conecta los dos.
2. **Bibliotecas** — Kaimaku le pregunta al servidor qué bibliotecas tienes y las localiza en el disco por su contenido. Da igual que Jellyfin las vea como `/media/peliculas` y en tu disco estén en `/mnt/user/datos/media/peliculas`, o que estén repartidas en varios discos. Desmarca las que no quieras (música, etc. ya vienen fuera).
3. **Listo** — elige cada cuánto busca temas y pulsa **Guardar y empezar**.

Y ya está. Kaimaku empieza a trabajar en segundo plano; puedes cerrar la web.

> ¿No usas Jellyfin ni Emby? En el paso 1 pulsa «No uso Jellyfin ni Emby» y elige tus carpetas con el explorador.

## 🐳 Portainer

**Stacks → Add stack → Web editor**, pega el `docker-compose.yml` de arriba (cambiando `/mnt/user` si hace falta) y **Deploy the stack**. Luego abre `http://IP-DE-TU-SERVIDOR:8098` y sigue el asistente.

## 🖥️ Unraid

1. Pestaña **Docker → Add Container**.
2. En **Template** (abajo del todo) pega:
   ```text
   https://raw.githubusercontent.com/nemesbak/kaimaku/main/unraid-template/kaimaku.xml
   ```
3. No cambies nada: pulsa **Apply**.
4. Abre la **WebUI** del contenedor y sigue el asistente.

## ¿Ya tenías Kaimaku instalado?

No tienes que cambiar nada: si tu `docker-compose.yml` monta tus medios en `/media` y tiene `JELLYFIN_API_KEY`/`EMBY_API_KEY`, `PUID`, etc., todo sigue funcionando exactamente igual al actualizar. Si quieres pasarte al modo nuevo (sin variables), sustituye tu `docker-compose.yml` por el de arriba, haz `docker compose up -d` y usa el botón **🛠** de la web. Ojo: el historial de "revisión" va ligado a la ruta de cada carpeta, así que al cambiar de `/media` a `/host` esas marcas se recalculan en el siguiente escaneo.

## Estructura de carpetas

Kaimaku instala los archivos con el esquema que ya usan Jellyfin/Emby para temas e intros, dentro de la carpeta de cada serie o película:

```text
peliculas/
└── Nombre de la película (2010)/
    ├── theme-music/
    │   └── song1.mp3          <- audio del tema, lo crea Kaimaku
    ├── backdrops/
    │   └── intro.mp4          <- vídeo del tema, lo crea Kaimaku
    └── Nombre de la película (2010).mkv   <- tus archivos, Kaimaku no los toca
```

Las carpetas y archivos que crea quedan con **el mismo dueño que la carpeta de la serie/película**, así que Sonarr, Radarr y Jellyfin pueden moverlos o borrarlos sin problemas de permisos. Si ya existía un archivo, se guarda una copia antes de sustituirlo (en `data/backups/`).

Activa la reproducción en cada cliente: en Jellyfin/Emby, **Ajustes → Pantalla → Bibliotecas → Reproducir temas / vídeos de fondo**. Si una serie tiene los dos, el vídeo tiene prioridad.

## Cómo funciona

**En segundo plano:** cada pocas horas (12 por defecto, se cambia en 🛠) Kaimaku recorre tus bibliotecas. Para cada serie o película sin tema busca en YouTube, puntúa los resultados (castellano, canal oficial, coincidencia de título y año, duración, que sea una cabecera y no un tráiler o un clip...) y:
- si el mejor candidato es fiable, lo instala solo y refresca la biblioteca en Jellyfin/Emby;
- si no, lo marca como **revisión** (👀 en el póster) sin instalar nada dudoso.

**En la web:** parrilla de pósters con filtros por biblioteca y estado. Al abrir una serie o película ves lo instalado y los candidatos (miniatura, canal, duración y barra de confianza); eliges uno o buscas/pegas tu propio enlace y pulsas **Instalar**. **Escanear ahora** lanza el escaneo en el momento, y **🕘 Actividad** muestra el progreso en vivo.

Opcional: en 🛠 puedes poner un bot de Telegram para recibir un resumen de cada escaneo (solo cuando hubo algo que contar).

## 🩺 Diagnóstico

El botón **⚙** muestra en vivo qué bibliotecas usa Kaimaku y cuántos títulos tiene cada una, si Jellyfin/Emby responden y aceptan el acceso, si Telegram está activo y cómo está configurado el escaneo. Para cambiar cualquier cosa, botón **🛠**.

## Actualizar

```bash
docker compose pull
docker compose up -d
```

## Desinstalar

```bash
docker compose down
```

Tu biblioteca no se toca; solo se borra el contenedor (y la carpeta `data/` si la borras tú).

## Solución de problemas

**El asistente dice «Kaimaku no ve tus archivos»**
La línea del volumen (`- /mnt/user:/host`) apunta a una carpeta que no existe o está vacía. Pon a la izquierda la carpeta donde guardas tus medios y ejecuta `docker compose up -d`.

**No encuentra mi Jellyfin/Emby**
Si está en otro equipo de tu red, escribe su dirección en el asistente (por ejemplo `192.168.1.10:8096`). Si te dice «no es administrador», usa una cuenta de administrador: Kaimaku la necesita para ver dónde están las bibliotecas.

**Una biblioteca sale con «⚠ No la encuentro»**
Esa carpeta no está dentro de lo que has montado en `/host` (por ejemplo, está en otro disco). Monta una carpeta más grande que la incluya, o pulsa **Elegir carpeta…** si ya está dentro.

**No veo pósters, solo iniciales**
Los pósters salen de tu Jellyfin/Emby: conéctalo en 🛠. Lo demás funciona igual sin él.

**Las descargas fallan con `HTTP Error 403`**
YouTube frena temporalmente cuando se le piden muchas cosas seguidas; Kaimaku reintenta y el siguiente escaneo lo vuelve a intentar. Si pasa siempre, actualiza la imagen (`docker compose pull && docker compose up -d`).

**El preview sale en blanco**
Algunos canales oficiales no permiten verse incrustados fuera de YouTube. Usa «Ver en YouTube ↗» o elige otro candidato.

## Avanzado: variables de entorno

Nada de esto hace falta, pero sigue funcionando (y si una variable tiene valor, manda sobre lo que pongas en la web):

| Variable | Para qué |
| --- | --- |
| `PUID` / `PGID` | Correr como ese usuario en vez de asignar el dueño automáticamente. |
| `JELLYFIN_URL` + `JELLYFIN_API_KEY`, `EMBY_URL` + `EMBY_API_KEY` | Conectar el servidor con API key en vez de desde la web. |
| `MEDIA_ROOTS` | Modo clásico con medios montados en `/media`: limitar a carpetas concretas (`/media/anime,/media/series`). |
| `AUTO_SCAN_INTERVAL_HOURS` | Horas entre escaneos (`0` = desactivado). |
| `AUTO_MIN_SCORE` | Confianza mínima (0–1) para instalar sin preguntar. Por defecto `0.75`. |
| `AUTO_ASSETS` | `audio`, `video` o `audio,video`. |
| `TG_BOT_TOKEN`, `TG_CHAT_ID`, `TG_TOPIC_ID` | Avisos por Telegram. |

## Licencia

[MIT](LICENSE)
