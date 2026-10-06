FROM denoland/deno:bin AS deno

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    APP_HOME=/app \
    DATA_DIR=/data \
    HOST_BASE=/host \
    MEDIA_BASE=/media \
    MEDIA_ROOTS= \
    PUID= \
    PGID= \
    UMASK=002

# yt-dlp needs a JS runtime to deobfuscate YouTube's signature cipher;
# without it, downloads fail with "HTTP Error 403: Forbidden".
# See https://github.com/yt-dlp/yt-dlp/wiki/EJS
COPY --from=deno /deno /usr/local/bin/deno

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8098
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8098/api/health', timeout=4)" || exit 1
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8098"]
