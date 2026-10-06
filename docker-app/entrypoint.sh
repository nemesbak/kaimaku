#!/bin/sh
# Dueño de los archivos que crea Kaimaku (theme-music/ y backdrops/):
# - Sin PUID (lo normal): no hay nada que configurar. Kaimaku corre como root
#   DENTRO del contenedor y cada carpeta/archivo que crea pasa a ser del mismo
#   dueño que la carpeta de la serie/película donde lo deja, así que
#   Sonarr/Radarr/Jellyfin pueden moverlos o borrarlos sin "Access denied".
# - Con PUID/PGID: corre como ese usuario, igual que en versiones anteriores.
set -e
umask "${UMASK:-002}"
mkdir -p "$DATA_DIR"
export HOME="$DATA_DIR"

if [ -n "$PUID" ] && [ "$PUID" != "0" ] && [ "$(id -u)" = "0" ]; then
  PGID="${PGID:-$PUID}"
  chown -R "$PUID:$PGID" "$DATA_DIR" 2>/dev/null || true
  # Autoarreglo para quien actualiza desde versiones que corrian como root
  # (en segundo plano, para no retrasar el arranque en bibliotecas grandes).
  if [ -d "$MEDIA_BASE" ]; then
    ( find "$MEDIA_BASE" -mindepth 3 -maxdepth 5 \( -path '*/theme-music*' -o -path '*/backdrops*' \) \
        ! -user "$PUID" -exec chown "$PUID:$PGID" {} + 2>/dev/null || true ) &
  fi
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups -- "$@"
fi
exec "$@"
