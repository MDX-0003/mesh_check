"""备份：data/raw（全部原始模型）+ tasks.json → 时间戳快照目录。

目的：模型是用 credit 换来的资产——代码有 bug 或需改进时直接复用本地模型，
绝不重新生成。生成批处理结束后自动触发（generate --no-backup 可跳过），
也可手动：uv run python -m meshq.tools.backup
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from meshq.core.common import DATA_DIR, load_config


def backup_root(cfg: dict, data_dir: Path = DATA_DIR) -> Path:
    """config [backup] dir 优先；空则回退 data/backup。"""
    configured = ((cfg.get("backup") or {}).get("dir") or "").strip()
    return Path(configured) if configured else (data_dir / "backup")


def run_backup(cfg: dict, data_dir: Path = DATA_DIR, log=print) -> Path | None:
    src_raw = data_dir / "raw"
    tasks_file = data_dir / "tasks.json"
    if not src_raw.exists():
        log("[backup] 无 data/raw，跳过")
        return None

    root = backup_root(cfg, data_dir)
    ts = time.strftime("%Y%m%d-%H%M%S")
    dest = root / ts
    i = 1
    while dest.exists():  # 同秒多次备份：追加序号
        dest = root / f"{ts}-{i}"
        i += 1
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_raw, dest / "raw")
    n_models = sum(1 for d in (dest / "raw").iterdir() if (d / "model.glb").is_file())

    total_bytes = sum(f.stat().st_size for f in (dest / "raw").rglob("*") if f.is_file())
    if tasks_file.exists():
        shutil.copy2(tasks_file, dest / "tasks.json")
    (dest / "BACKUP-MANIFEST.json").write_text(json.dumps({
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "models": n_models,
        "raw_bytes": total_bytes,
        "ledger_statuses": _ledger_statuses(tasks_file),
    }, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    log(f"[backup] {n_models} 个模型 → {dest}（{total_bytes / 1e6:.1f} MB）")
    return dest


def _ledger_statuses(tasks_file: Path) -> dict[str, int]:
    if not tasks_file.exists():
        return {}
    out: dict[str, int] = {}
    for rec in json.loads(tasks_file.read_text(encoding="utf-8")):
        s = rec.get("status", "?")
        out[s] = out.get(s, 0) + 1
    return out


def latest_backup(cfg: dict, data_dir: Path = DATA_DIR) -> Path | None:
    root = backup_root(cfg, data_dir)
    if not root.exists():
        return None
    snaps = sorted(d for d in root.iterdir() if d.is_dir())
    return snaps[-1] if snaps else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="备份原始模型与台账")
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = ap.parse_args(argv)
    dest = run_backup(load_config(), args.data_dir)
    return 0 if dest else 1


if __name__ == "__main__":
    sys.exit(main())
