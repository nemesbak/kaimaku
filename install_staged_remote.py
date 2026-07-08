#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-root", default="staged")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--backup-existing", action="store_true")
    parser.add_argument("--backup-root", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    stage_root = Path(args.stage_root)
    backup_root = Path(args.backup_root) if args.backup_root else stage_root.parent / "kaimaku-backups" / datetime.now().strftime("%Y%m%d-%H%M%S-install")
    manifest = json.loads((stage_root / "manifest.json").read_text(encoding="utf-8"))
    installed = []
    skipped = []
    missing = []
    backed_up = []

    for item in manifest:
        remote_dir = Path(item["remote_path"])
        stage_dir = item.get("stage_dir")
        if not remote_dir.exists():
            missing.append({"name": item["name"], "path": str(remote_dir)})
            continue
        for file_info in item.get("files", []):
            src_dir = stage_dir or Path(file_info["source"]).parent.name
            src = stage_root / src_dir / file_info["name"]
            dst = remote_dir / file_info["name"]
            if not src.exists():
                missing.append({"name": item["name"], "path": str(src)})
                continue
            if dst.exists() and not args.overwrite:
                skipped.append({"name": item["name"], "target": str(dst), "reason": "exists"})
                continue
            if not args.dry_run:
                if dst.exists() and args.backup_existing:
                    backup = backup_root / remote_dir.name / file_info["name"]
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dst, backup)
                    backed_up.append({"name": item["name"], "source": str(dst), "backup": str(backup)})
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            installed.append({"name": item["name"], "target": str(dst), "asset": file_info["asset"]})

    result = {
        "installed": installed,
        "skipped": skipped,
        "missing": missing,
        "backed_up": backed_up,
        "counts": {
            "installed": len(installed),
            "skipped": len(skipped),
            "missing": len(missing),
            "backed_up": len(backed_up),
        },
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
