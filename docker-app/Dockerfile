FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    APP_HOME=/app \
    DATA_DIR=/data \
    MEDIA_ROOTS=/media

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app

EXPOSE 8098
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8098"]
