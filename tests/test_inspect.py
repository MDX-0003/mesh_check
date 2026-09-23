import json
from pathlib import Path

import numpy as np
import trimesh

from meshq.stages import metrics as ins        # 03_inspect 改名而来（产物是 metrics.jsonl）


def make_two_piece_glb(tmp_path: Path) -> Path:
    """主体箱（1m）+ 悬浮小箱（5cm，对角线比 0.05 < 0.1）合成 GLB。"""
    big = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
    big.apply_translation((0, 0, 0.5))
    small = trimesh.creation.box(extents=(0.05, 0.05, 0.05))
    small.apply_translation((0.8, 0, 1.6))
    combo = trimesh.util.concatenate([big, small])
    out = tmp_path / "two_piece.glb"
    combo.export(out)
    return out


def test_inspect_two_piece_glb(tmp_path):
    glb = make_two_piece_glb(tmp_path)
    rec = ins.inspect_glb(glb)
    assert rec["engine"] == "trimesh"
    assert rec["n_pieces"] == 2
    assert rec["n_small_pieces_default"] == 1
    assert 0 < rec["fragment_face_ratio_default"] <= 1  # 两箱各 12 面 → 面数比 0.5（面数比≠尺寸比）
    assert rec["pieces"][0]["n_faces"] >= rec["pieces"][1]["n_faces"]  # 降序（合成件面数相等）
    assert rec["watertight"] is True  # 两只箱体各自水密
    assert rec["raw_bbox"]["extents"] != [0, 0, 0]


def test_weld_merges_duplicated_vertices():
    box = trimesh.creation.box(extents=(1, 1, 1))
    dup = trimesh.Trimesh(
        vertices=np.concatenate([box.vertices, box.vertices + 1e-9]),
        faces=np.concatenate([box.faces, box.faces + len(box.vertices)]),
        process=False,
    )
    assert len(dup.vertices) == 2 * len(box.vertices)
    welded = ins.weld(dup)
    assert len(welded.vertices) == len(box.vertices)
    assert len(welded.faces) == 2 * len(box.faces)  # 面数不变，仅顶点合并


def test_manifold_box_has_zero_non_manifold_edges(tmp_path):
    glb = make_two_piece_glb(tmp_path)
    rec = ins.inspect_glb(glb)
    assert rec["non_manifold_edges"] == 0


def test_main_appends_to_metrics_jsonl(tmp_path, monkeypatch):
    glb = make_two_piece_glb(tmp_path)
    raw = tmp_path / "raw" / "pX@standard"
    raw.mkdir(parents=True)
    glb.rename(raw / "model.glb")

    metrics_file = tmp_path / "metrics.jsonl"
    monkeypatch.setattr(ins, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ins, "METRICS_FILE", metrics_file)

    ins.main([])
    assert metrics_file.exists()
    recs = [json.loads(l) for l in metrics_file.read_text(encoding="utf-8").splitlines()]
    assert len(recs) == 1 and recs[0]["key"] == "pX@standard"

    ins.main([])  # 幂等：不重复追加
    recs = [json.loads(l) for l in metrics_file.read_text(encoding="utf-8").splitlines()]
    assert len(recs) == 1


def test_main_keys_force_preserves_other_records(tmp_path, monkeypatch):
    """安全线：--keys + --force 只重算列出的键，绝不丢弃表中其它记录（T2 探针依赖）。"""
    glb = make_two_piece_glb(tmp_path)
    raw = tmp_path / "raw" / "pX@standard"
    raw.mkdir(parents=True)
    glb.rename(raw / "model.glb")

    metrics_file = tmp_path / "metrics.jsonl"
    metrics_file.write_text(json.dumps(
        {"key": "pY@standard", "n_faces": 1}) + "\n", encoding="utf-8")
    monkeypatch.setattr(ins, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ins, "METRICS_FILE", metrics_file)

    assert ins.main(["--keys", "pX@standard", "--force"]) == 0
    recs = [json.loads(l) for l in metrics_file.read_text(encoding="utf-8").splitlines()]
    assert {r["key"] for r in recs} == {"pX@standard", "pY@standard"}
    assert next(r for r in recs if r["key"] == "pY@standard")["n_faces"] == 1
