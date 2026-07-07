#!/bin/sh
set -eu

curl -fsS http://127.0.0.1:8098/ >/tmp/ats-index.html
curl -fsS http://127.0.0.1:8098/api/items >/tmp/ats-items.json
curl -fsS http://127.0.0.1:8098/api/jobs >/tmp/ats-jobs.json

python3 - <<'PY'
import json
items = json.load(open("/tmp/ats-items.json", encoding="utf-8"))
jobs = json.load(open("/tmp/ats-jobs.json", encoding="utf-8"))
print("items", len(items.get("items", [])))
print("jobs", len(jobs.get("jobs", [])))
PY

docker logs --tail 20 anime-theme-sync
