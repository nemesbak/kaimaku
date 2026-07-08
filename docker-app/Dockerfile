FROM denoland/deno:bin AS deno

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    APP_HOME=/app \
    DATA_DIR=/data \
    MEDIA_ROOTS=/media

# yt-dlp needs a JS runtime to deobfuscate YouTube's signature cipher;
# without it, downloads fail with "HTTP Error 403: Forbidden".
# See https://github.com/yt-dlp/yt-dlp/wiki/EJS
COPY --from=deno /deno /usr/local/bin/deno

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app

EXPOSE 8098
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8098/api/items', timeout=4)" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8098"]
