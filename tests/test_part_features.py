"""part_features：特征提取的守卫。

三件事最值得钉住：
1. 尺寸聚类的**反链式合并**——固定百分比容差已被实测证伪（±15% 下 0.7301 与 0.8402
   相差 15.07%），改用"与簇首比较"，本测试防止有人换回逐元素比较；
2. 双引擎匹配必须带 diag 一同比较——只比面数会在 bpy 599/591 与 trimesh 598/590
   之间产生歧义互配（p01@smart-topology 实测）；
3. 邻近/贴片字段在合成件上的取值方向（远超 vs 贴合、贴片 vs 非贴片）。
"""

import json

import numpy as np
import pytest
import trimesh

from meshq.core.part_features import (ENGINE_DIAG_TOL, bpy_pieces_for, extract,
                           match_bpy_partition, size_clusters)

PARTS_CFG = {
    "weld_digits": 5,
    "iso_ratio": 0.05,
    "diag_ratio_big": 0.30,
    "min_faces": 24,
    "solid_min_faces": 24,
    "volume_eps": 1e-06,
    "sibling_cluster_ratio": 1.25,
    "contact_tol": 0.005,
    "bbox_pin_tol": 0.01,
    "sample_points": 600,
    "enable_a0": True,
    "require_bpy_agreement": True,
    "enable_a1": False,
}


# ---------------------------------------------------------------- 尺寸聚类

def test_size_clusters_groups_equal_sized_parts():
    """腿/撑这类同尺寸件应落同一簇（p01@smart 实测尺寸带 0.73–0.91 相邻很近）。"""
    sizes = size_clusters([1.0, 0.73, 0.7301, 0.84, 0.9056, 2.0], 1.25)
    assert sizes[1] == sizes[2] == sizes[3] == sizes[4] == 4   # 0.73/0.7301/0.84/0.9056 同簇
    assert sizes[0] == 1                                       # 1.0 与簇首 0.73 相差 >25%
    assert sizes[5] == 1                                       # 2.0 单独


def test_size_clusters_does_not_chain_merge():
    """逐元素比较会把 1.0/1.2/1.44/1.73 连成一簇；与簇首比较应断成两簇。"""
    sizes = size_clusters([1.0, 1.2, 1.44, 1.73], 1.25)
    assert sizes[0] == sizes[1] == 2
    assert sizes[2] == sizes[3] == 2


def test_size_clusters_empty_and_single():
    assert size_clusters([], 1.25) == []
    assert size_clusters([0.5], 1.25) == [1]


def test_size_clusters_groups_degenerate_zero_diags():
    """零对角线（退化件）不应因比较基准为 0 而被拆散或除零。"""
    assert size_clusters([0.0, 0.0, 1.0], 1.25) == [2, 2, 1]


# ---------------------------------------------------------------- 双引擎匹配

def _trimesh_pieces(*faces_diags):
    """(faces, diag) 列表 → 组件记录；第一件为主组件。"""
    return [{"n_faces": f, "diag": d} for f, d in faces_diags]


def test_match_bpy_partition_flags_piece_only_trimesh_sees():
    """p01@smart-topology 实测：bpy 8 件 / trimesh 11 件，差额正是 3 个 2 面件（diag 0.0044）。"""
    trimesh_pieces = _trimesh_pieces((816, 0.766), (728, 0.694), (2, 0.0044))
    bpy_pieces = _trimesh_pieces((816, 0.766), (728, 0.694), (599, 0.9917))
    assert match_bpy_partition(trimesh_pieces, bpy_pieces) == [0, 1, None]


def test_match_bpy_partition_tolerates_off_by_one_faces():
    """焊接算法不同源，同组件面数会漂 ±1（599/598、591/590 实测），必须仍算匹配。"""
    trimesh_pieces = _trimesh_pieces((598, 0.9917), (590, 0.9921))
    bpy_pieces = _trimesh_pieces((599, 0.9917), (591, 0.9921))
    assert match_bpy_partition(trimesh_pieces, bpy_pieces) == [0, 1]


def test_match_bpy_partition_needs_diag_to_disambiguate():
    """只比面数会歧义互配：bpy 有两个面数在 ±2 内的候选，diag 才能挑出对的那个。"""
    trimesh_pieces = _trimesh_pieces((598, 0.40))
    bpy_pieces = _trimesh_pieces((599, 0.9917), (597, 0.40))
    assert match_bpy_partition(trimesh_pieces, bpy_pieces) == [1]   # 不是 0
    assert ENGINE_DIAG_TOL == 0.02


def test_match_bpy_partition_does_not_normalize_by_each_engine_main():
    """反例守卫：816 面件在 trimesh 侧是 diag 最大者（比值 1.0）、在 bpy 侧不是（0.772）。

    若用"各自的主组件对角线"归一，这条会误判为不匹配——故实现必须比绝对 diag。
    """
    trimesh_pieces = _trimesh_pieces((816, 0.766), (598, 0.992))
    bpy_pieces = _trimesh_pieces((599, 0.9917), (816, 0.766))
    assert match_bpy_partition(trimesh_pieces, bpy_pieces) == [1, 0]


def test_match_bpy_partition_survives_dense_mesh_face_loss():
    """bpy 焊接会塌掉稠密网格的近退化面：p12 实测差 598 面，仍必须算同一件。"""
    trimesh_pieces = _trimesh_pieces((644972, 2.419361))
    bpy_pieces = _trimesh_pieces((644374, 2.419361))
    assert match_bpy_partition(trimesh_pieces, bpy_pieces) == [0]


def test_match_bpy_partition_rejects_tiny_shard_against_big_part():
    """相对面数容差不得把 T2 的 2 面片匹配到 bpy 的 301 面件上。"""
    trimesh_pieces = _trimesh_pieces((2, 0.0044))
    bpy_pieces = _trimesh_pieces((301, 0.4493))
    assert match_bpy_partition(trimesh_pieces, bpy_pieces) == [None]


def test_match_bpy_partition_without_bpy_data():
    pieces = _trimesh_pieces((10, 1.0))
    assert match_bpy_partition(pieces, []) == [None]


def test_bpy_pieces_for_prefers_render_checks(tmp_path):
    raw = tmp_path / "p01@standard"
    raw.mkdir()
    (raw / "blender_checks.json").write_text(
        json.dumps({"pieces": [{"n_faces": 10, "diag": 1.0}]}), encoding="utf-8")
    (raw / "render").mkdir()
    (raw / "render" / "parts_legend.json").write_text(
        json.dumps({"pieces": [{"n_faces": 99, "diag": 9.0}]}), encoding="utf-8")
    assert bpy_pieces_for(raw)[0]["n_faces"] == 10      # 渲染即有者优先


def test_bpy_pieces_for_missing_files(tmp_path):
    raw = tmp_path / "p01@standard"
    raw.mkdir()
    assert bpy_pieces_for(raw) == []


# ---------------------------------------------------------------- 提取

def make_two_box_glb(tmp_path):
    """主体 1m 立方 + 远处小立方（0.05）。"""
    big = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
    big.apply_translation((0, 0, 0.5))
    small = trimesh.creation.box(extents=(0.05, 0.05, 0.05))
    small.apply_translation((0.8, 0, 1.6))
    out = tmp_path / "pX@standard" / "model.glb"
    out.parent.mkdir(parents=True)
    trimesh.util.concatenate([big, small]).export(out)
    return out


def test_extract_reports_main_and_isolated_piece(tmp_path):
    rec = extract(make_two_box_glb(tmp_path), PARTS_CFG, bpy_pieces=[], diag_ratio=0.10)
    assert rec["key"] == "pX@standard"
    assert rec["n_pieces"] == 2
    assert rec["main_rank"] == 0
    main, far = rec["parts"][0], rec["parts"][1]
    assert main["is_main"] is True
    assert far["is_main"] is False
    # 小件远在 0.8 之外：相对主组件对角线（√3≈1.73）约 0.16
    assert far["gap_ratio"] is not None and far["gap_ratio"] > 0.1
    assert main["gap_ratio"] <= far["gap_ratio"]
    assert far["bbox_pinned"] is False          # 有厚度的立方不是贴片
    assert far["engine_seen_by_bpy"] is False   # 无 bpy 数据 → 一律未见证
    assert far["n_faces"] == main["n_faces"] == 12
    assert rec["engine_partition_match"] == 0


def test_extract_marks_engine_agreement_when_bpy_sees_it(tmp_path):
    glb = make_two_box_glb(tmp_path)
    bpy = [{"n_faces": 12, "diag": 1.732}, {"n_faces": 12, "diag": 0.0866}]
    rec = extract(glb, PARTS_CFG, bpy_pieces=bpy, diag_ratio=0.10)
    assert rec["engine_partition_match"] == 2


def test_extract_single_piece_has_no_peer_fields(tmp_path):
    """单组件模型：无处可比 → 邻近字段为 None，不得报错或伪造 0。"""
    box = trimesh.creation.box(extents=(1, 1, 1))
    out = tmp_path / "pY@standard" / "model.glb"
    out.parent.mkdir(parents=True)
    box.export(out)
    rec = extract(out, PARTS_CFG, bpy_pieces=[], diag_ratio=0.10)
    part = rec["parts"][0]
    assert rec["n_pieces"] == 1
    assert part["gap_to_main"] is None and part["gap_ratio"] is None
    assert part["contact_frac"] == 0.0


# ---------------------------------------------------------------- --keys+--force 安全线

def test_main_keys_force_preserves_other_records(tmp_path, monkeypatch):
    """--keys + --force 只重算列出的键，parts.jsonl 其余记录必须保留（T2 探针依赖）。"""
    import meshq.core.part_features as pfmod
    raw_a = tmp_path / "raw" / "pA@standard"
    raw_a.mkdir(parents=True)
    glb_a = raw_a / "model.glb"
    glb_a.write_bytes(b"x")  # extract 已被打桩，不读真 GLB

    parts_file = tmp_path / "parts.jsonl"
    parts_file.write_text(json.dumps(
        {"key": "pB@standard", "n_pieces": 2}) + "\n", encoding="utf-8")

    monkeypatch.setattr(pfmod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(pfmod, "PARTS_FILE", parts_file)
    monkeypatch.setattr(pfmod, "load_config",
                        lambda: {"parts": dict(PARTS_CFG),
                                 "detect": {"diag_ratio": 0.1}})
    monkeypatch.setattr(pfmod, "iter_glbs",
                        lambda data_dir, keys, fixed: [("pA@standard", glb_a)])
    monkeypatch.setattr(pfmod, "bpy_pieces_for", lambda parent: [])
    monkeypatch.setattr(pfmod, "extract",
                        lambda glb, parts_cfg, bpy_pieces, diag_ratio:
                        {"key": glb.parent.name, "n_pieces": 1,
                         "bpy_n_pieces": 1, "engine_partition_match": True,
                         "boundary_edges": 0, "non_manifold_edges": 0})

    assert pfmod.main(["--keys", "pA@standard", "--force"]) == 0
    recs = [json.loads(l) for l in parts_file.read_text(encoding="utf-8").splitlines()]
    assert {r["key"] for r in recs} == {"pA@standard", "pB@standard"}
    assert next(r for r in recs if r["key"] == "pB@standard")["n_pieces"] == 2
