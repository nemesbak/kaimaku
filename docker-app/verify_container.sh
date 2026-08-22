#!/bin/sh
set -eu

curl -fsS http://127.0.0.1:8098/ >/tmp/kmk-index.html
curl -fsS http://127.0.0.1:8098/api/library >/tmp/kmk-library.json
curl -fsS http://127.0.0.1:8098/api/jobs >/tmp/kmk-jobs.json
curl -fsS http://127.0.0.1:8098/api/status >/tmp/kmk-status.json

python3 - <<'PY'
import json
library = json.load(open("/tmp/kmk-library.json", encoding="utf-8"))
jobs = json.load(open("/tmp/kmk-jobs.json", encoding="utf-8"))
status = json.load(open("/tmp/kmk-status.json", encoding="utf-8"))
print("items", len(library.get("items", [])))
print("libraries", library.get("libraries"))
print("jobs", len(jobs.get("jobs", [])))
print("auto_scan", status.get("auto_scan"))
PY

docker logs --tail 20 kaimaku
