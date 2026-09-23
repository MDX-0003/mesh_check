"""detect：v2 检出编排的守卫（PLAN-04 §三/§五步 5）。

合成网格 + 手工特征行，覆盖：三类检出行各就各位、共位分型（重叠/灰区/界面）、
合并优先级（review 先于 keep，tier 相同按缺陷类）、批次过滤、findings/islands
双产物落盘，以及"只检出不删除"（无 auto tier、不产删除队列）。
"""

import json

import numpy as np
import trimesh

import meshq.stages.detect as detect
from meshq.stages.detect import run_one
from meshq.core.detectors import FloatingIslandDetector

CFG = {"parts": {
    "weld_digits": 5, "island_eps_ratio": 0.03, "island_query_points": 500,
    "island_surface_samples": 300,
    "enable_a0": True, "require_bpy_agreement": True, "enable_a1": False,
    "iso_ratio": 0.05, "diag_ratio_big": 0.30, "group_min_siblings": 2,
    "min_faces": 24, "solid_min_faces": 24, "volume_eps": 1e-06,
    "sibling_cluster_ratio": 1.25,
    "coloc_overlap_tol": 0.001, "coloc_true": 0.6, "coloc_partial": 0.2,
    "coloc_surface_samples": 500,
}}


def box(extents=(2.0, 2.0, 2.0), translate=(0.0, 0.0, 0.0)):
    m = trimesh.creation.box(extents=extents)
    m.apply_translation(translate)
    return m


def patch(translate=(0.0, 0.0, 1.0), half=0.4):
    """贴在主盒顶面 z=1 上的无厚度方片（真重叠锚点几何）。"""
    v = np.array([[-half, -half, 0.0], [half, -half, 0.0],
                  [half, half, 0.0], [-half, half, 0.0]], dtype=float)
    m = trimesh.Trimesh(vertices=v, faces=np.array([[0, 1, 2], [0, 2, 3]]))
    m.apply_translation(translate)
    return m


def part_row(rank, **over):
    base = {"rank": rank, "n_faces": 12, "is_main": False, "bbox_pinned": False,
            "engine_seen_by_bpy": True, "gap_ratio": 0.001, "diag_ratio": 0.1,
            "siblings": 0, "vol_ratio": 0.01, "watertight": True, "euler": 2,
            "nearest_rank": 0}
    base.update(over)
    return base


def make_model_a(engine_of_floater=True):
    """主体 + 远处悬浮件(1) + 封闭内部件(2) + 贴合小件(3) + 顶面复制片(4)。"""
    return {"main_rank": 0, "n_pieces": 5, "parts": [
        part_row(0, is_main=True, diag_ratio=1.0),
        part_row(1, gap_ratio=0.4, engine_seen_by_bpy=engine_of_floater),
        part_row(2, engine_seen_by_bpy=False, vol_ratio=1e-8),
        part_row(3, watertight=False, vol_ratio=1e-9),
        part_row(4, n_faces=2, engine_seen_by_bpy=False, vol_ratio=1e-8),
    ]}


def write_glb(tmp_path, key, meshes):
    d = tmp_path / "raw" / key
    d.mkdir(parents=True, exist_ok=True)
    trimesh.util.concatenate(meshes).export(d / "model.glb")


def make_world(tmp_path):
    """模型 A（三类检出）+ 模型 B（界面贴合存活）+ 模型 C（standard 批，应被过滤）。"""
    write_glb(tmp_path, "mA@smart-topology",
              [box(), box((0.3, 0.3, 0.3), (3.0, 0, 0)),
               box((0.3, 0.3, 0.3), (0.5, 0, 0)), box((0.3, 0.3, 0.3), (1.15, 0, 0)),
               patch()])
    write_glb(tmp_path, "mB@smart-topology",
              [box(), box((0.6, 0.6, 0.6), (0.5, 0.5, 1.3))])   # 叠立方：界面贴合
    write_glb(tmp_path, "mC@standard",
              [box(), box((0.3, 0.3, 0.3), (3.0, 0, 0))])
    records = [
        {"key": "mA@smart-topology", **make_model_a()},
        {"key": "mB@smart-topology", "main_rank": 0, "n_pieces": 2, "parts": [
            part_row(0, is_main=True, diag_ratio=1.0),
            part_row(1, n_faces=12, engine_seen_by_bpy=False, diag_ratio=0.9,
                     vol_ratio=0.05, watertight=True)]},
        {"key": "mC@standard", "main_rank": 0, "n_pieces": 2, "parts": [
            part_row(0, is_main=True, diag_ratio=1.0),
            part_row(1, gap_ratio=0.4)]},
    ]
    parts_file = tmp_path / "parts.jsonl"
    parts_file.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8")
    return parts_file


def by_rank(rows):
    return {r["rank"]: r for r in rows}


# ---------------------------------------------------------------- run_one

def test_run_one_produces_three_defect_classes(tmp_path):
    make_world(tmp_path)
    feature = make_model_a()
    rows, islands = run_one("mA@smart-topology", feature, CFG,
                            FloatingIslandDetector(), data_dir=tmp_path)
    rows = by_rank(rows)
    assert rows[1]["defect_class"] == "floating"          # 远处悬浮件（岛层）
    assert rows[1]["entry"] == "island"
    assert rows[2]["defect_class"] == "floating"          # 封闭内部件：几何上独立岛
    assert rows[4]["defect_class"] == "co_located"        # 顶面复制片 → 重叠型共位
    assert rows[4]["evidence"]["co_located_subtype"] == "overlap"
    assert rows[4]["tier"] == "review"
    assert rows[3]["defect_class"] == "other"             # 贴合小件：级联 A1-off
    assert rows[3]["entry"] == "A1-off"
    assert all(r["tier"] in ("review", "keep") for r in rows.values())
    assert islands["n_islands"] == 3                      # 主岛(含 3,4) + 悬浮件 + 封闭件


def test_run_one_interface_survives_without_competition(tmp_path):
    """模型 B：叠立方 + 单引擎证据，无其他检出竞争 → 界面贴合行保留（keep）。"""
    make_world(tmp_path)
    feat = {"main_rank": 0, "n_pieces": 2, "parts": [
        part_row(0, is_main=True, diag_ratio=1.0),
        part_row(1, n_faces=12, engine_seen_by_bpy=False, diag_ratio=0.9,
                 vol_ratio=0.05, watertight=True)]}
    rows, islands = run_one("mB@smart-topology", feat, CFG,
                            FloatingIslandDetector(), data_dir=tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["defect_class"] == "co_located"
    assert r["evidence"]["co_located_subtype"] == "interface"
    assert r["tier"] == "keep"                            # 非缺陷，不进复核队列
    assert 0.1 < r["metrics"]["overlap_frac"] < 0.25      # ≈ 底面/全面积 = 1/6
    assert islands["n_islands"] == 1                      # 面接触 → 同岛


def test_run_one_merge_prefers_review_over_keep(tmp_path):
    """合并优先级：review 的悬浮检出压过 keep 的界面贴合（tier 先于缺陷类）。"""
    make_world(tmp_path)
    feature = make_model_a(engine_of_floater=False)       # 悬浮件同时有共位证据
    rows, _ = run_one("mA@smart-topology", feature, CFG,
                      FloatingIslandDetector(), data_dir=tmp_path)
    r1 = by_rank(rows)[1]
    assert r1["defect_class"] == "floating"               # review 悬浮 > keep 界面
    assert len([r for r in rows if r["rank"] == 1]) == 1  # 去重后只留一条
    # rank4 顶面复制片：co_located(review) 对 other(review)，按缺陷类共位胜出
    assert by_rank(rows)[4]["defect_class"] == "co_located"


# ---------------------------------------------------------------- main 批处理

def test_main_batch_filter_and_artifacts(tmp_path, monkeypatch, capsys):
    parts_file = make_world(tmp_path)
    monkeypatch.setattr(detect, "DATA_DIR", tmp_path)
    monkeypatch.setattr(detect, "load_config", lambda: CFG)

    out = tmp_path / "findings.jsonl"
    islands_out = tmp_path / "islands.jsonl"
    rc = detect.main(["--batch", "smart-topology",
                          "--parts-file", str(parts_file),
                          "--out", str(out), "--islands-out", str(islands_out)])
    assert rc == 0
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    # 批次过滤；B 只剩界面贴合行（keep），A 四条 review
    assert {r["key"] for r in rows} == {"mA@smart-topology", "mB@smart-topology"}
    assert not any(r["tier"] == "auto" for r in rows)             # 只检出不删除
    assert sum(1 for r in rows if r["tier"] == "keep"
               and r["evidence"].get("co_located_subtype") == "interface") == 1
    islands = [json.loads(l) for l in islands_out.read_text(encoding="utf-8").splitlines()
               if l.strip()]
    assert [i["key"] for i in islands] == ["mA@smart-topology", "mB@smart-topology"]
    assert islands[0]["n_islands"] == 3 and islands[1]["n_islands"] == 1
    text = capsys.readouterr().out
    assert "悬浮 2 条（1 模型）" in text and "共位 2 条（2 模型）" in text \
        and "非核心 1 条（1 模型）" in text


def test_main_keys_filter(tmp_path, monkeypatch):
    parts_file = make_world(tmp_path)
    monkeypatch.setattr(detect, "DATA_DIR", tmp_path)
    monkeypatch.setattr(detect, "load_config", lambda: CFG)
    out = tmp_path / "f2.jsonl"
    rc = detect.main(["--keys", "mA@smart-topology", "mC@standard",
                          "--parts-file", str(parts_file),
                          "--out", str(out),
                          "--islands-out", str(tmp_path / "i2.jsonl")])
    assert rc == 0
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert {r["key"] for r in rows} == {"mA@smart-topology", "mC@standard"}
