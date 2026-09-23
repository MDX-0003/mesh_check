"""mesh_ops：trimesh 侧共享几何操作的守卫。

重点不是"函数能跑"，而是三件容易静默出错的事：
1. 开放边界与真非流形必须是两个互不重叠的类别，且两者之和 = 历史口径（无损可回退）；
2. 包围盒贴片判定必须**同时**要求"扁"与"贴边"——只判贴边会误伤天然处于模型外缘的部件；
3. 顶点抽样必须确定性——随机抽样会让同一模型每次跑出不同特征值，判别从此不可复现。
"""

import numpy as np
import pytest
import trimesh

from meshq.core.geometry import bounds_stats
from meshq.core.mesh_ops import (bbox_overlap_ratio, bbox_pinned_piece, edge_classes,
                      piece_record, sample_points, split_components, weld)


def bowtie():
    """三面共用一条边 (0,1) → 真非流形 1 条；其余 6 条边各只被一个面使用。"""
    v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1]], dtype=float)
    f = np.array([[0, 1, 2], [0, 1, 3], [1, 0, 4]])
    return trimesh.Trimesh(vertices=v, faces=f, process=False)


def open_plane():
    """单层平面片（两个三角）：四条边是开放边界，零真非流形。"""
    return trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
                           faces=[[0, 1, 2], [0, 2, 3]], process=False)


def legacy_metric(mesh: trimesh.Trimesh) -> int:
    """历史口径：相邻面计数 != 2 的边数（= 开放边界 + 真非流形）。"""
    _, counts = np.unique(mesh.edges_sorted, axis=0, return_counts=True)
    return int((counts != 2).sum())


# ---------------------------------------------------------------- 边分类

def test_manifold_box_has_neither():
    ec = edge_classes(trimesh.creation.box(extents=(1, 1, 1)))
    assert ec["boundary_edges"] == 0
    assert ec["non_manifold_edges"] == 0


def test_open_shell_counts_as_boundary_not_non_manifold():
    """低模常见形态：开放边界不是缺陷（PLAN-03 §1.2 的核心论点）。"""
    ec = edge_classes(open_plane())
    assert ec["boundary_edges"] == 4
    assert ec["non_manifold_edges"] == 0


def test_true_non_manifold_counted_separately():
    ec = edge_classes(bowtie())
    assert ec["non_manifold_edges"] == 1
    assert ec["boundary_edges"] == 6


@pytest.mark.parametrize("mesh_factory", [lambda: trimesh.creation.box(extents=(1, 1, 1)),
                                          open_plane, bowtie])
def test_split_is_lossless_vs_legacy_metric(mesh_factory):
    """拆分不丢信息：新增两字段之和 == 历史单值口径。"""
    mesh = mesh_factory()
    ec = edge_classes(mesh)
    assert ec["boundary_edges"] + ec["non_manifold_edges"] == legacy_metric(mesh)


def test_empty_mesh_edge_classes():
    assert edge_classes(trimesh.Trimesh(vertices=[], faces=[])) == {
        "boundary_edges": 0, "non_manifold_edges": 0}


# ---------------------------------------------------------------- 组件

def test_split_components_sorted_by_faces():
    big = trimesh.creation.box(extents=(1, 1, 1))
    small = trimesh.creation.box(extents=(0.1, 0.1, 0.1))
    small.apply_translation((3, 0, 0))
    combo = trimesh.util.concatenate([small, big])   # 故意把小件放前面
    comps = split_components(combo)
    assert len(comps) == 2
    assert len(comps[0].faces) >= len(comps[1].faces)


def test_piece_record_box_is_solid_euler_two():
    rec = piece_record(trimesh.creation.box(extents=(2, 2, 2)))
    assert rec["watertight"] is True
    assert rec["euler"] == 2
    assert rec["non_manifold_edges"] == 0
    assert rec["diag"] == pytest.approx(bounds_stats([-1, -1, -1], [1, 1, 1])["diag"])
    assert rec["volume"] == pytest.approx(8.0)


def test_weld_merges_duplicated_vertices():
    box = trimesh.creation.box(extents=(1, 1, 1))
    dup = trimesh.Trimesh(
        vertices=np.concatenate([box.vertices, box.vertices + 1e-9]),
        faces=np.concatenate([box.faces, box.faces + len(box.vertices)]),
        process=False)
    assert len(dup.vertices) == 2 * len(box.vertices)
    merged = weld(dup)
    assert len(merged.vertices) == len(box.vertices)


# ---------------------------------------------------------------- 抽样

def test_sample_points_is_deterministic_and_keeps_endpoints():
    v = np.arange(3000, dtype=float).reshape(1000, 3)
    a, b = sample_points(v, 100), sample_points(v, 100)
    assert a.shape == (100, 3)
    assert np.array_equal(a, b)                     # 两次调用必须一致
    assert np.array_equal(a[0], v[0]) and np.array_equal(a[-1], v[-1])


def test_sample_points_returns_all_when_fewer_than_n():
    v = np.zeros((5, 3))
    assert len(sample_points(v, 600)) == 5
    assert len(sample_points(np.zeros((0, 3)), 600)) == 0


# ---------------------------------------------------------------- 包围盒贴片

MODEL_BOUNDS = (np.array([-0.5, -0.5, -0.5]), np.array([0.5, 0.5, 0.5]))


def test_flat_piece_pinned_to_model_bound_is_flagged():
    """§1.7 的实测形态：扁平且某一轴坐标恰好等于模型边界（Y = ±0.5）。"""
    piece = (np.array([0.0, -0.5, 0.0]), np.array([0.02, -0.5, 0.02]))
    assert bbox_pinned_piece(piece, MODEL_BOUNDS, 0.01) is True


def test_flat_piece_in_the_middle_is_not_flagged():
    """扁但不在边界上 → 不是贴片（可能是模型内部的一块薄板）。"""
    piece = (np.array([0.0, -0.01, 0.0]), np.array([0.02, 0.01, 0.02]))
    assert bbox_pinned_piece(piece, MODEL_BOUNDS, 0.01) is False


def test_solid_piece_at_bound_is_not_flagged():
    """只判"贴边"会误伤：模型顶部的实体部件天然贴着 max Z，但它不扁。"""
    piece = (np.array([-0.1, -0.1, 0.3]), np.array([0.1, 0.1, 0.5]))
    assert bbox_pinned_piece(piece, MODEL_BOUNDS, 0.01) is False


def test_bbox_pinned_disabled_when_tol_non_positive():
    piece = (np.array([0.0, -0.5, 0.0]), np.array([0.02, -0.5, 0.02]))
    assert bbox_pinned_piece(piece, MODEL_BOUNDS, 0.0) is False


# ---------------------------------------------------------------- bbox 交叠

def test_bbox_overlap_ratio_full_when_contained():
    inner = (np.array([-0.1, -0.1, -0.1]), np.array([0.1, 0.1, 0.1]))
    assert bbox_overlap_ratio(inner, MODEL_BOUNDS) == pytest.approx(1.0)


def test_bbox_overlap_ratio_zero_when_disjoint():
    far = (np.array([5.0, 5.0, 5.0]), np.array([6.0, 6.0, 6.0]))
    assert bbox_overlap_ratio(far, MODEL_BOUNDS) == 0.0


def test_bbox_overlap_ratio_zero_for_degenerate_piece():
    """退化件（bbox 体积 0）的"包含"无意义，返回 0 而不是除零。"""
    flat = (np.array([0.0, 0.0, 0.0]), np.array([0.1, 0.0, 0.1]))
    assert bbox_overlap_ratio(flat, MODEL_BOUNDS) == 0.0
