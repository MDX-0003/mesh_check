import json
from pathlib import Path

from meshq.tools import backup as bk


def make_cfg(tmp_path):
    return {"backup": {"dir": str(tmp_path / "backups")}}


def make_raw(tmp_path, n=2):
    raw = tmp_path / "raw"
    for i in range(n):
        d = raw / f"p{i}@standard"
        d.mkdir(parents=True)
        (d / "model.glb").write_bytes(b"glb-bytes" * 10)
    (tmp_path / "tasks.json").write_text(json.dumps(
        [{"key": f"p{i}@standard", "status": "SUCCEEDED"} for i in range(n)]),
        encoding="utf-8")


def test_backup_root_configured_vs_default(tmp_path):
    assert bk.backup_root(make_cfg(tmp_path), tmp_path) == tmp_path / "backups"
    assert bk.backup_root({"backup": {"dir": ""}}, tmp_path) == tmp_path / "backup"
    assert bk.backup_root({}, tmp_path) == tmp_path / "backup"


def test_run_backup_snapshots_raw_and_ledger(tmp_path):
    make_raw(tmp_path)
    dest = bk.run_backup(make_cfg(tmp_path), tmp_path, log=lambda *a: None)
    assert dest is not None and dest.exists()
    assert (dest / "raw" / "p0@standard" / "model.glb").is_file()
    assert (dest / "tasks.json").is_file()
    manifest = json.loads((dest / "BACKUP-MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["models"] == 2
    assert manifest["ledger_statuses"] == {"SUCCEEDED": 2}


def test_run_backup_without_raw_returns_none(tmp_path):
    assert bk.run_backup(make_cfg(tmp_path), tmp_path, log=lambda *a: None) is None


def test_latest_backup_returns_newest(tmp_path):
    make_raw(tmp_path)
    cfg = make_cfg(tmp_path)
    first = bk.run_backup(cfg, tmp_path, log=lambda *a: None)
    second = bk.run_backup(cfg, tmp_path, log=lambda *a: None)
    assert bk.latest_backup(cfg, tmp_path) in (first, second)
    assert bk.latest_backup(cfg, tmp_path).name >= first.name
