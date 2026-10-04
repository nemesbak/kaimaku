#!/bin/sh
# Kaimaku corre SIEMPRE como usuario normal (PUID:PGID), nunca como root.
# Asi las carpetas theme-music/ y backdrops/ que crea dentro de tu biblioteca
# quedan con el mismo dueno que el resto de tus medios, y Sonarr/Radarr/Jellyfin
# pueden moverlas o borrarlas sin errores de "Access denied".
set -e
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
umask "${UMASK:-002}"

if [ "$(id -u)" = "0" ] && [ "$PUID" != "0" ]; then
  mkdir -p "$DATA_DIR"
  chown -R "$PUID:$PGID" "$DATA_DIR" 2>/dev/null || true
  # Autoarreglo para quien actualiza desde versiones que corrian como root:
  # devuelve al usuario correcto las carpetas y archivos de temas ya creados (en segundo
  # plano, para no retrasar el arranque en bibliotecas grandes).
  ( find "$MEDIA_BASE" -mindepth 3 -maxdepth 5 \( -path '*/theme-music*' -o -path '*/backdrops*' \) \
      ! -user "$PUID" -exec chown "$PUID:$PGID" {} + 2>/dev/null || true ) &
  export HOME="$DATA_DIR"
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups -- "$@"
fi
exec "$@"
