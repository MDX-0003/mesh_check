"""trimesh 侧几何操作的单一入口：焊接、连通域、组件统计、边分类、包围盒贴片判定。

**为什么有这个模块**：焊接参数、组件统计与边分类原先在指标脚本与检出脚本里各写一份
（`merge_vertices(digits_vertex=5)` + `split()` + 组装 pieces 的循环重复两遍，且两侧字段还不一致），
part_features 再写第三份就必然漂移。口径定义（边分类、主组件选取、diag/体积）统一在
geometry.py，两侧引擎共用；本模块只放"怎么用 trimesh 拿到这些量"。

**与 bpy 侧（bpy_geom.py）的关系**：两套引擎的算法**不同源**，同一模型可能给出不同组件数——
实测 p01@smart-topology：bpy 8 件 / trimesh 11 件；p18@standard：bpy 23 件 / trimesh 24 件。
根因是两者的"焊接"不是同一件事：

  bpy     `remove_doubles(threshold=1e-4)` —— **邻近合并**（距离小于阈值即合并）
  trimesh `merge_vertices(digits_vertex=5)` —— **十进制量化**后精确去重（不是邻近合并）

所以**不要试图用 digits 去模拟 bpy 的阈值**（实测反例：p18 在 digits=4 下反而从 24 拆成 29 件，
量化位移导致去重失败）。差异本身是判别信号（PLAN-03 D12：存在性依赖焊接容差的候选不得自动删），
因此这里不对齐、只把各自口径写明。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import trimesh

from meshq.core.geometry import bounds_stats

WELD_DIGITS = 5  # merge_vertices 位置量化位数（~1e-5）；同 metrics 历史口径与调研 recipe


def weld(mesh: trimesh.Trimesh, digits: int = WELD_DIGITS) -> trimesh.Trimesh:
    """焊接 UV 接缝分裂的顶点（量化去重，见模块 docstring 与 bpy 侧的差异说明）。"""
    merged = mesh.copy()
    merged.merge_vertices(merge_tex=True, merge_norm=True, digits_vertex=digits)
    return merged


def split_components(mesh: trimesh.Trimesh) -> list[trimesh.Trimesh]:
    """连通域拆分，按面数降序。面数相等时保持 trimesh 的原始顺序（稳定）。"""
    comps = list(mesh.split(only_watertight=False))
    comps.sort(key=lambda c: -len(c.faces))
    return comps


def edge_classes(mesh: trimesh.Trimesh) -> dict[str, int]:
    """开放边界（相邻面计数=1）与真非流形（计数>2）分列。"""
    from meshq.core.geometry import classify_edge_counts

    if len(mesh.faces) == 0:
        return {"boundary_edges": 0, "non_manifold_edges": 0}
    _, counts = np.unique(mesh.edges_sorted, axis=0, return_counts=True)
    return classify_edge_counts(int(c) for c in counts)


def piece_record(comp: trimesh.Trimesh) -> dict[str, Any]:
    """单个组件的完整特征行（判别用；`meshq/stages/metrics.py::piece_records` 是同数据的瘦记录）。"""
    bmin, bmax = comp.bounds
    stats = bounds_stats(bmin, bmax)
    ec = edge_classes(comp)
    return {
        "n_verts": int(len(comp.vertices)),
        "n_faces": int(len(comp.faces)),
        "diag": stats["diag"],
        "volume": stats["volume"],
        "watertight": bool(comp.is_watertight),
        "winding_consistent": bool(comp.is_winding_consistent),
        "euler": int(comp.euler_number),
        "boundary_edges": ec["boundary_edges"],
        "non_manifold_edges": ec["non_manifold_edges"],
    }


def sample_points(vertices: np.ndarray, n: int) -> np.ndarray:
    """确定性均匀抽样（含首尾点），供邻近查询使用。

    必须确定性：特征表要可复现、要能跨次比对；随机抽样会让同一模型每次跑出不同特征值。
    """
    if len(vertices) == 0:
        return np.zeros((0, 3), dtype=float)
    if len(vertices) <= n:
        return np.asarray(vertices, dtype=float)
    idx = np.linspace(0, len(vertices) - 1, n).astype(int)
    return np.asarray(vertices, dtype=float)[idx]


def bbox_pinned_piece(piece_bounds, model_bounds, tol: float) -> bool:
    """组件是否"贴在模型包围盒平面上"——PLAN-03 §1.7 真实性门之一。

    判据：存在某个轴，组件在该轴上**是扁的**（extent <= tol）**且**该轴坐标**贴着模型包围盒边界**
    （min 或 max 与模型对应边界相差 <= tol）。tol 由调用方按主组件对角线的比例给出。

    实测依据：p01@smart-topology 的 3 个"2 面件"bbox 的 Y 恰好等于 ±0.5，而模型原始 bbox 的 Y
    范围正是 [-0.5, +0.5]，三角形面积 2.9e-06~3.5e-05（渲染下亚像素）。它们是退化微三角，
    不是悬浮碎屑——删掉视觉上什么都不变，当"缺陷被检出"写进报告就是把文件格式的退化产物
    当成了模型缺陷。

    只判"贴边"会误伤正常件（例如模型顶部的部件天然贴着 max Z），因此**必须同时要求"扁"**。
    """
    if tol <= 0:
        return False
    for axis in range(3):
        lo_p, hi_p = float(piece_bounds[0][axis]), float(piece_bounds[1][axis])
        lo_m, hi_m = float(model_bounds[0][axis]), float(model_bounds[1][axis])
        flat = (hi_p - lo_p) <= tol
        at_bound = (abs(lo_p - lo_m) <= tol) or (abs(hi_p - hi_m) <= tol)
        if flat and at_bound:
            return True
    return False


def bbox_overlap_ratio(piece_bounds, other_bounds) -> float:
    """组件 bbox 与另一组件 bbox 的交集体积 / 组件自身 bbox 体积（0..1）。

    组件 bbox 退化（体积 0，例如平面片）时返回 0.0——"包含"对退化件无意义。
    本量在主组件是薄壳时不可靠（它只看 bbox），只作参考不作判据。
    """
    lo = np.maximum(np.asarray(piece_bounds[0], dtype=float),
                    np.asarray(other_bounds[0], dtype=float))
    hi = np.minimum(np.asarray(piece_bounds[1], dtype=float),
                    np.asarray(other_bounds[1], dtype=float))
    ov = np.clip(hi - lo, 0.0, None)
    own = bounds_stats(piece_bounds[0], piece_bounds[1])["volume"]
    if own <= 0:
        return 0.0
    return float(np.prod(ov)) / own
