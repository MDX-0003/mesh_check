"""几何领域纯逻辑：组件尺寸统计、边分类、小碎片分类、入库判定。

不依赖 bpy/trimesh/numpy——Blender 内置 Python（bpy_geom / render_one）与 uv 虚拟环境
（metrics / detect / mesh_ops / part_features）共用同一份定义。
阈值语义见 PLAN-00 D2：相对主组件的 bbox 对角线比例为主判据。

**两处口径（PLAN-03 §3，改这里等于同时改双引擎）**

1. 边按"相邻面计数"分类：计数=1 是**开放边界**，计数>2 是**真非流形**。
   两者语义完全不同——smart-topology 低模大量存在开放边界，那是模型品类属性而非缺陷；
   历史实现把两者合成一个数（`计数 != 2`），导致 T2 模型会被整体判成"有缺陷"（实测
   p01@smart-topology：35 条里 29 条是边界）。**凡是要当缺陷信号用的场合只吃真非流形。**
2. 主组件 = **bbox 对角线最大**的组件，不是面数最多。面数最多 ≠ 空间最大：实测
   p01@smart-topology 按面数选出的主组件只有 816 面，而另两个部件的 bbox 对角线是它的
   1.29 倍；相对阈值以主组件对角线为分母，分母必须取"空间最大"才有稳定语义。

改本模块必改 tests/test_geometry.py。
"""

from __future__ import annotations

from typing import Any, Iterable


def bounds_stats(bmin, bmax) -> dict[str, float]:
    """由 bbox 两端点算对角线与 bbox 体积——**组件规模口径的唯一定义**。

    bpy 侧（bpy_geom.find_pieces 拿到顶点坐标）与 trimesh 侧（mesh_ops 拿到 comp.bounds）
    都收敛到这里：历史上两侧各写一遍（纯 Python 的 max-min 与 numpy 的 norm/prod），
    口径漂移了就是判定阈值静默变化，而这是主判据的定义。
    """
    sx = float(bmax[0]) - float(bmin[0])
    sy = float(bmax[1]) - float(bmin[1])
    sz = float(bmax[2]) - float(bmin[2])
    diag = (sx * sx + sy * sy + sz * sz) ** 0.5
    return {"diag": diag, "volume": sx * sy * sz}


def bbox_stats(coords: list[tuple[float, float, float]]) -> dict[str, float]:
    """点集的 bbox 对角线与 bbox 体积（口径见 bounds_stats）。"""
    if not coords:
        return {"diag": 0.0, "volume": 0.0}
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    zs = [c[2] for c in coords]
    return bounds_stats((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def classify_edge_counts(counts: Iterable[int]) -> dict[str, int]:
    """无向边的"相邻面计数"序列 → 开放边界与真非流形分列。

    计数=1：只被一个面用到 → 网格的开放边界（薄壳/单面片的正常属性）。
    计数>2：三个及以上面共用一条边 → 真非流形，几何上不可流形化。
    两者之和即历史口径 `计数 != 2`，故本函数不丢信息、可无损回退。
    """
    boundary = 0
    non_manifold = 0
    for c in counts:
        if c == 1:
            boundary += 1
        elif c > 2:
            non_manifold += 1
    return {"boundary_edges": boundary, "non_manifold_edges": non_manifold}


def main_piece_index(pieces: list[dict[str, Any]]) -> int:
    """主组件下标 = bbox 对角线最大者；同尺寸时取面数多者，仍相同取靠前者。

    空列表返回 0（调用方按"无组件"自行处理）。
    """
    if not pieces:
        return 0
    return max(range(len(pieces)),
               key=lambda i: (pieces[i].get("diag") or 0.0,
                              pieces[i].get("n_faces") or 0,
                              -i))


def classify_pieces(pieces: list[dict[str, Any]], diag_ratio: float) -> None:
    """就地标记 pieces 的 is_small 与 diag_ratio_to_main（主组件由 main_piece_index 选定）。

    规则：主组件永不标记；非主组件 diag < 主组件 diag × diag_ratio → is_small=True。
    成对等大部件（如一双鞋的两只）diag 比接近 1，不会被误标（见单测）。
    """
    if not pieces:
        return
    main_i = main_piece_index(pieces)
    main_diag = pieces[main_i]["diag"]
    for i, piece in enumerate(pieces):
        if i == main_i or main_diag <= 0:
            piece["is_small"] = False
            piece["diag_ratio_to_main"] = None if main_diag <= 0 else piece["diag"] / main_diag
            continue
        ratio = piece["diag"] / main_diag
        piece["diag_ratio_to_main"] = ratio
        piece["is_small"] = ratio < diag_ratio


def compute_verdict(
    *,
    n_pieces: int,
    n_small_pieces: int,
    fragment_face_ratio: float,
    non_manifold_edges: int,
    frag_reject_ratio: float,
) -> str:
    """三档判定（PLAN-00 D2 初版规则）：直接入库 / 修复后入库 / 拒绝。

    `non_manifold_edges` 必须是**收窄口径**（计数>2），不得传开放边界——
    否则 T2 低模会全批落到"修复后入库"（PLAN-03 §1.2）。
    """
    if n_small_pieces == 0 and non_manifold_edges == 0:
        return "直接入库"
    if fragment_face_ratio > frag_reject_ratio:
        return "拒绝"
    if n_small_pieces > 0 or non_manifold_edges > 0:
        return "修复后入库"
    return "直接入库"
