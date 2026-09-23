"""v2 检测关节（PLAN-04 §三）：模型上下文、检测器协议与注册表。

**骨架约定**（刻意最小化，不做插件框架——没有自动发现、没有配置 DSL，第 4 个
检测器出现时再升级）：

    检测器 = 类属性 `name` / `defect_class` + 方法 `detect(ctx, cfg) -> list[dict]`
    （返回 findings.make_finding 产出的检出行）；注册就是 `REGISTRY` 这个 list。

## ModelContext（层 0 取证输入的骨架侧封装）

惰性加载：weld → 组件列表 → 对角线，组件 rank 与 parts.jsonl 同源
（weld(parts.weld_digits) → split_components 面数降序下标）。特征行（parts.jsonl
记录）可选注入：岛层只吃几何，共位检测器吃特征行，注入后检测器可取 siblings 等
群体字段。

## FloatingIslandDetector（层 1 悬浮检出，island_sweep 原型产品化）

原型（data/island_sweep.py，500 点 KD 树近似）产品化时的三处替换：
1. **精确最小距离**：查询点 = 顶点确定性采样（island_query_points 上限）∪ 表面
   加密采样（R2 序列重心坐标，无 RNG，见 surface_samples），目标侧用
   `_SurfIndex`（质心 KD 树 + k 近邻候选 + 精确点-三角形距离）求最近表面距离。
   采样 + 候选截断只会**高估**真实最小距离 → 组件显得更"远"→ 偏向多报不漏报，
   方向保守。不用 trimesh.proximity（rtree BVH）：73 万面半壳实测构建+查询过慢
   （p06@standard 单模型 941s，见 .claude/memory）。
2. **bbox 预筛**：AABB 间距是真实距离的**下界**，> ε 的组件对不可能同岛，直接
   跳过精确查询；只有 bbox 间距 <= ε 的对（贴合/近贴对）才做精确查询——恰好是
   必须算准的那部分。
3. **ε 取值见 config `island_eps_ratio`**：原型的 500 点近似系统性高估间隙
   （p04/p06@standard 的真碎片采样间隙 3.05%~4.78%，精确值更低），固定 3% 的
   平台区结论部分是采样伪影；v2 以精确距离重标定（PLAN-04 §四决策 3 修订记录）。

判定语义：ε 单连接聚类 → 岛；含主组件（bbox 对角线最大）的岛为主岛，其余岛整岛
记为悬浮候选（defect_class=floating，tier=review——**只检出不删除**，PLAN-04
决策 1）。岛内多组件 → 整组位移叙事（C1 型）；单组件 → 悬浮碎片叙事（A2 型）。

改本模块必改 tests/test_detectors.py。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from meshq.core.findings import (CLASS_CO_LOCATED, CLASS_FLOATING, TIER_KEEP, TIER_REVIEW,
                      make_finding)
from meshq.core.geometry import bounds_stats
from meshq.core.mesh_ops import sample_points, split_components, weld

# R2 低差异序列常数（黄金比例的二维推广）；表面采样的确定性锚，勿改——
# 改了等于换一把尺子，岛明细跨版本不可比
_R2_A = 0.7548776662466927
_R2_B = 0.5698402909980532

# 每个查询点取多少个质心最近的候选三角形；候选子集只会高估距离（方向保守）
_KNN_CANDIDATES = 24


class ModelContext:
    """单个模型的惰性上下文：GLB 路径 + 可选特征行 → 网格 / 组件 / 对角线。

    组件与 rank 都是懒加载缓存；GLB 解析一次，weld/split 各跑一次。
    """

    def __init__(self, key: str, glb_path: Path, feature: dict | None = None,
                 weld_digits: int = 5):
        self.key = key
        self.glb_path = Path(glb_path)
        self.feature = feature
        self._weld_digits = int(weld_digits)
        self._mesh: trimesh.Trimesh | None = None
        self._comps: list[trimesh.Trimesh] | None = None

    @property
    def mesh(self) -> trimesh.Trimesh:
        if self._mesh is None:
            self._mesh = weld(trimesh.load(self.glb_path, force="mesh"),
                              self._weld_digits)
        return self._mesh

    @property
    def components(self) -> list[trimesh.Trimesh]:
        if self._comps is None:
            self._comps = split_components(self.mesh)
        return self._comps

    @property
    def diag(self) -> float:
        return bounds_stats(*self.mesh.bounds)["diag"]

    @property
    def main_rank(self) -> int:
        """主组件 rank：优先取特征行（parts.jsonl 口径），缺省按对角线最大现算。"""
        if self.feature and self.feature.get("main_rank") is not None:
            return int(self.feature["main_rank"])
        comps = self.components
        if not comps:
            return 0
        diags = [bounds_stats(*c.bounds)["diag"] for c in comps]
        return max(range(len(comps)),
                   key=lambda i: (diags[i], len(comps[i].faces), -i))

    def part_row(self, rank: int) -> dict[str, Any]:
        """特征行里该 rank 的组件记录（无特征行时返回空 dict）。"""
        for p in (self.feature or {}).get("parts", []):
            if int(p.get("rank", -1)) == rank:
                return p
        return {}


# ---------------------------------------------------------------- 几何核心

def surface_samples(mesh: trimesh.Trimesh, count: int) -> np.ndarray:
    """面积加权、**确定性**的表面采样点（无 RNG，跨次运行可比）。

    按累计面积等距取目标点（系统性采样），面内位置用 R2 低差异序列的重心坐标——
    同一面被命中多次时得到分散的不同点。trimesh 自带 sample_surface 依赖随机数，
    不满足特征/岛明细"可复现、可跨次比对"的仓库约定（同 mesh_ops.sample_points）。
    """
    if count <= 0 or len(mesh.faces) == 0:
        return np.zeros((0, 3), dtype=float)
    areas = np.asarray(mesh.area_faces, dtype=float)
    cum = np.cumsum(areas)
    total = float(cum[-1])
    if total <= 0:
        return np.zeros((0, 3), dtype=float)
    targets = (np.arange(count, dtype=float) + 0.5) * (total / count)
    face_idx = np.clip(np.searchsorted(cum, targets, side="left"),
                       0, len(areas) - 1)
    tris = mesh.triangles[face_idx]
    # 面内局部序号：同面第 k 次命中 → 第 k 组重心坐标
    order = np.argsort(face_idx, kind="stable")
    sorted_faces = face_idx[order]
    starts = np.searchsorted(sorted_faces, sorted_faces, side="left")
    local = np.empty(count, dtype=float)
    local[order] = np.arange(count) - starts
    u = ((local + 0.5) * _R2_A) % 1.0
    v = ((local + 0.5) * _R2_B) % 1.0
    flip = (u + v) > 1.0
    u[flip] = 1.0 - u[flip]
    v[flip] = 1.0 - v[flip]
    return (tris[:, 0] + u[:, None] * (tris[:, 1] - tris[:, 0])
            + v[:, None] * (tris[:, 2] - tris[:, 0]))


def query_points(comp: trimesh.Trimesh, vertex_cap: int,
                 surface_count: int) -> np.ndarray:
    """组件参与最小距离查询的点集：顶点确定性采样 ∪ 表面加密采样。"""
    pts = sample_points(np.asarray(comp.vertices, dtype=float), vertex_cap)
    extra = surface_samples(comp, surface_count)
    return np.vstack([pts, extra]) if len(extra) else pts


def bbox_gap_matrix(comps: list[trimesh.Trimesh]) -> np.ndarray:
    """组件对 AABB 间距矩阵（真实距离的**下界**；相交/贴合为 0）。"""
    bounds = np.asarray([c.bounds for c in comps], dtype=float)
    lo, hi = bounds[:, 0, :], bounds[:, 1, :]
    axis_gap = np.maximum(np.maximum(lo[None, :, :] - hi[:, None, :],
                                     lo[:, None, :] - hi[None, :, :]), 0.0)
    return np.linalg.norm(axis_gap, axis=2)


def _seg_dist(p: np.ndarray, s0: np.ndarray, s1: np.ndarray) -> np.ndarray:
    """点到线段精确距离（广播形 p:(m,k,3), s0/s1:(m,k,3) → (m,k)）。退化段回退顶点距。"""
    d = s1 - s0
    l2 = (d * d).sum(-1)
    safe = np.where(l2 > 0, l2, 1.0)
    t = np.clip(((p - s0) * d).sum(-1) / safe, 0.0, 1.0)
    proj = s0 + t[..., None] * d
    dist = np.sqrt(((p - proj) ** 2).sum(-1))
    return np.where(l2 > 0, dist,
                    np.sqrt(((p - s0) ** 2).sum(-1)))


def _pt_tri_dist(p: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """点到三角形集的精确距离：p:(m,3)，tris:(m,k,3,3) → (m,k)。

    距离 = min(面内投影距（投影落在三角形内时），三条边的线段距)。顶点距被
    线段距覆盖（线段端点即顶点），故不单列——合起来就是精确的点到三角形距离。
    """
    a, b, c = tris[:, :, 0, :], tris[:, :, 1, :], tris[:, :, 2, :]   # (m,k,3)
    pp = p[:, None, :]                                               # (m,1,3)

    # 面内：正交投影 + 重心坐标内外判（proj = p − t·n，t 为沿法线的有符号距离）
    n = np.cross(b - a, c - a)
    nn = (n * n).sum(-1)
    safe_nn = np.where(nn > 0, nn, 1.0)
    t = ((pp - a) * n).sum(-1) / safe_nn
    proj = pp - t[..., None] * n
    v0, v1, v2 = b - a, c - a, proj - a
    d00 = (v0 * v0).sum(-1); d01 = (v0 * v1).sum(-1); d11 = (v1 * v1).sum(-1)
    d20 = (v2 * v0).sum(-1); d21 = (v2 * v1).sum(-1)
    den = d00 * d11 - d01 * d01
    safe_den = np.where(np.abs(den) > 0, den, 1.0)
    s = (d11 * d20 - d01 * d21) / safe_den
    u = (d00 * d21 - d01 * d20) / safe_den
    inside = (nn > 0) & (np.abs(den) > 0) & (s >= 0) & (u >= 0) & (s + u <= 1)
    face_d = np.sqrt(((pp - proj) ** 2).sum(-1))
    face_d = np.where(inside, face_d, np.inf)

    edges = (_seg_dist(pp, a, b), _seg_dist(pp, a, c), _seg_dist(pp, b, c))
    return np.minimum.reduce([face_d, *edges])


class _SurfIndex:
    """组件表面的最近距离索引：三角形质心 cKDTree + k 近邻候选 + 精确点-面距。

    为什么不用 trimesh.proximity（rtree BVH）：73 万面半壳实测构建+查询过慢
    （p06@standard 单模型 941s）。质心树每查询点只取 k 个最近质心的候选面，
    极端非均匀网格下可能漏掉真最近面 → 结果只会上界高估，方向保守（多报不漏报）；
    均匀网格（生成资产的普遍形态）下与真值一致。
    """

    def __init__(self, mesh: trimesh.Trimesh):
        self._tris = np.asarray(mesh.triangles, dtype=float)   # (f,3,3)
        self._tree = cKDTree(self._tris.mean(axis=1))

    def min_distance(self, pts: np.ndarray) -> float:
        if len(self._tris) == 0 or len(pts) == 0:
            return float("inf")
        return float(self.min_distance_per_point(pts).min())

    def min_distance_per_point(self, pts: np.ndarray) -> np.ndarray:
        """逐查询点的最近表面距离（(m,)）。"""
        if len(self._tris) == 0:
            return np.full(len(pts), np.inf)
        if len(pts) == 0:
            return np.zeros(0)
        knn = min(_KNN_CANDIDATES, len(self._tris))
        _, idx = self._tree.query(np.asarray(pts, dtype=float), k=knn)
        idx = np.asarray(idx).reshape(len(pts), knn)
        return _pt_tri_dist(np.asarray(pts, dtype=float),
                            self._tris[idx]).min(axis=1)


def _union_find_groups(n: int, edges: list[tuple[int, int]]) -> list[list[int]]:
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in edges:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = [sorted(g) for g in groups.values()]
    out.sort(key=lambda g: g[0])
    return out


def precise_pair_distances(comps: list[trimesh.Trimesh], eps: float,
                           vertex_cap: int, surface_count: int,
                           indices: list[_SurfIndex] | None = None
                           ) -> dict[tuple[int, int], float]:
    """bbox 间距 <= eps 的组件对的精确最小距离（双向取小）。

    只覆盖"可能同岛"的对：AABB 间距是下界，> eps 的对不可能被 ε 聚类合并。
    indices 可注入复用已建好的 _SurfIndex（跨岛 gap 计算与聚类共用）。
    返回 {(i, j): 距离}（i < j）。
    """
    bbox_gap = bbox_gap_matrix(comps)
    n = len(comps)
    close = [(i, j) for i in range(n) for j in range(i + 1, n)
             if bbox_gap[i, j] <= eps]
    if not close:
        return {}
    if indices is None:
        indices = [_SurfIndex(c) for c in comps]
    qp = [query_points(c, vertex_cap, surface_count) for c in comps]
    pair: dict[tuple[int, int], float] = {}
    for i, j in close:
        pair[(i, j)] = min(indices[j].min_distance(qp[i]),
                           indices[i].min_distance(qp[j]))
    return pair


def cluster_islands(n: int, pair: dict[tuple[int, int], float],
                    eps: float) -> list[list[int]]:
    """ε 单连接聚类 → 岛列表（每岛 = rank 升序列表，岛间按最小 rank 排序）。"""
    edges = [(i, j) for (i, j), d in pair.items() if d <= eps]
    return _union_find_groups(n, edges)


def _min_cross_gap(source: list[int], others: list[int],
                   pair: dict[tuple[int, int], float],
                   bbox_gap: np.ndarray, indices: list[_SurfIndex],
                   qp: list[np.ndarray]) -> float | None:
    """岛到其余几何的精确最小距离。

    分支定界：候选对按 bbox 下界升序，逐对精确查询（有缓存用缓存）；一旦某对的
    bbox 下界 >= 当前最优，后面的对不可能更小，直接停。source 为全集（无其余
    几何）时返回 None。
    """
    candidates = sorted(((bbox_gap[i, j], i, j) for i in source for j in others),
                        key=lambda t: t[0])
    if not candidates:
        return None
    best = None
    for bound, i, j in candidates:
        if best is not None and bound >= best:
            break
        key = (min(i, j), max(i, j))
        d = pair.get(key)
        if d is None:
            d = min(indices[j].min_distance(qp[i]),
                    indices[i].min_distance(qp[j]))
            pair[key] = d
        if best is None or d < best:
            best = d
    return best


# ---------------------------------------------------------------- 检测器

def surface_overlap_fraction(comps: list[trimesh.Trimesh], rank: int,
                             neighbor_rank: int, tol: float, n_samples: int,
                             indices: list[_SurfIndex] | None = None) -> float:
    """组件表面与指定邻件表面的**面积重叠率**（共位分型的核心度量）。

    定义：该组件表面上落入"与邻件表面间距 <= tol"的面积占比。采样按面积加权
    （点占比 ≈ 面积占比），距离为精确点-三角形距离。测量误差方向：采样与候选
    截断只会漏判重合点（估计值是真值的下界）——判为重合的必然真重合，不冤枉。

    阈值锚点（判定标准与几何依据见 `publish/2026-09-22/交付说明.md` 第三节的共位分型）：
    - 立方体叠立方体的合法界面 = 底面/全面积 = 1/6 ≈ 0.17 → 界面判定上限 0.2；
    - 整面贴合的极端扁平件 ≈ 0.5 → 任何界面形态到不了 0.6 → 真重叠下限 0.6；
    - tol 取 1e-3 对角线 = 渲染深度精度量级（焊接重合尺度 1e-4 之下全覆盖）。

    无效输入（邻件缺失、组件无面、采样失败）返回 0.0——方向上按"非重叠"处理，
    不会把界面误判成真重叠。
    """
    n = len(comps)
    if rank == neighbor_rank or not (0 <= rank < n) or not (0 <= neighbor_rank < n):
        return 0.0
    me = comps[rank]
    if len(me.faces) == 0:
        return 0.0
    pts = surface_samples(me, n_samples)
    if len(pts) == 0:
        return 0.0
    index = (indices or [_SurfIndex(c) for c in comps])[neighbor_rank]
    if len(index._tris) == 0:
        return 0.0
    dists = index.min_distance_per_point(pts)
    return float(np.mean(dists <= tol))


class CoLocatedDetector:
    """层 2 共位检出 + 分型：A0 证据入围，面积重叠率定性（PLAN-04 §三 + 2026-09-22 分型）。

    两段式：
    ① 入围（证据，来自 parts.jsonl 特征行）：①仅单引擎可见（bpy 1e-4 焊接下并入
       本体——表面贴近到焊接尺度）②包围盒贴片（无厚度导出产物）。两种机制在此
       无法区分，都要进第②段。
    ② 分型（测量，surface_overlap_fraction）：面积重叠率 = 组件表面与最近邻组件
       表面重合的面积占比。≥ coloc_true → 重叠型共位（真 z-fighting 候选，review）；
       ≥ coloc_partial → 部分重叠（灰区，review）；低于 → 界面贴合（T2 多组件拆分
       的正常形态，**非缺陷**，tier=keep——进"导出特性"度量，不占复核队列）。

    只检出不删除（PLAN-04 决策 1）。
    """

    name = "co_located"
    defect_class = CLASS_CO_LOCATED

    # 分型结果 → (tier, 理由前缀)
    _SUBTYPE_TIER = {"overlap": TIER_REVIEW, "partial": TIER_REVIEW,
                     "interface": TIER_KEEP}

    def detect(self, ctx: ModelContext, cfg: dict) -> list[dict]:
        parts_cfg = cfg["parts"]
        if not bool(parts_cfg.get("enable_a0", True)):
            return []
        require_bpy = bool(parts_cfg.get("require_bpy_agreement", True))
        tol = float(parts_cfg["coloc_overlap_tol"])
        t_true = float(parts_cfg["coloc_true"])
        t_partial = float(parts_cfg["coloc_partial"])
        n_samples = int(parts_cfg["coloc_surface_samples"])

        comps = ctx.components
        indices: list[_SurfIndex] = []
        rows = []
        for part in (ctx.feature or {}).get("parts", []):
            if part.get("is_main"):
                continue
            pinned = bool(part.get("bbox_pinned"))
            single_engine = require_bpy and not bool(part.get("engine_seen_by_bpy"))
            if not (pinned or single_engine):
                continue
            rank = int(part["rank"])
            neighbor = part.get("nearest_rank")
            overlap = 0.0
            if neighbor is not None and rank < len(comps):
                if not indices:
                    indices = [_SurfIndex(c) for c in comps]
                overlap = surface_overlap_fraction(
                    comps, rank, int(neighbor), tol, n_samples, indices=indices)
            subtype = ("overlap" if overlap >= t_true else
                       "partial" if overlap >= t_partial else "interface")
            # 理由串 = 检出时的简洁事实记录；白话展示文本由 05/06 渲染时经
            # finding_reason_text 从下述数字字段即时生成（文案改动无需重跑检出）
            rows.append(make_finding(
                key=ctx.key, detector=self.name, defect_class=self.defect_class,
                rank=rank,
                reason=f"与组件 #{neighbor} 表面重叠 {overlap:.0%}",
                tier=self._SUBTYPE_TIER[subtype], entry="A0",
                n_faces=part.get("n_faces"),
                evidence={"bbox_pinned": pinned,
                          "engine_seen_by_bpy": bool(part.get("engine_seen_by_bpy")),
                          "require_bpy_agreement": require_bpy,
                          "co_located_subtype": subtype,
                          "neighbor_rank": neighbor},
                metrics={"overlap_frac": round(overlap, 4),
                         "coloc_partial_threshold": t_partial,
                         "gap_ratio": part.get("gap_ratio"),
                         "diag_ratio": part.get("diag_ratio"),
                         "vol_ratio": part.get("vol_ratio"),
                         "bbox_thinness": part.get("bbox_thinness"),
                         "contact_frac": part.get("contact_frac"),
                         "siblings": part.get("siblings")}))
        return rows


class FloatingIslandDetector:
    """层 1 悬浮检出：视觉岛聚类，非主岛整岛记 floating 候选（只检出不删除）。"""

    name = "islands"
    defect_class = CLASS_FLOATING

    def detect(self, ctx: ModelContext, cfg: dict) -> list[dict]:
        return self.rows_from_report(ctx, self.island_report(ctx, cfg))

    def rows_from_report(self, ctx: ModelContext, report: dict) -> list[dict]:
        """岛明细 → 悬浮检出行（编排器先取 island_report 落盘，再复用本方法出检出）。"""
        rows = []
        for isl in report["islands"]:
            if isl["is_main"]:
                continue
            group = len(isl["ranks"]) > 1
            for rank in isl["ranks"]:
                part = ctx.part_row(rank)
                faces = part.get("n_faces")
                if faces is None:
                    faces = int(len(ctx.components[rank].faces))
                reason = (f"所在视觉岛与主体分离：最近距离 {isl['gap_ratio']} 倍对角线"
                          f"（阈值 {report['eps_ratio']}），岛内 {isl['shards']} 件")
                rows.append(make_finding(
                    key=ctx.key, detector=self.name, defect_class=self.defect_class,
                    rank=rank, reason=reason, tier=TIER_REVIEW, entry="island",
                    n_faces=faces,
                    evidence={"island_id": isl["id"], "island_ranks": isl["ranks"]},
                    metrics={"gap_ratio": isl["gap_ratio"],
                             "eps_ratio": report["eps_ratio"],
                             "island_shards": isl["shards"],
                             "island_face_share": isl["share"],
                             "siblings": part.get("siblings"),
                             "diag_ratio": part.get("diag_ratio")}))
        return rows

    def island_report(self, ctx: ModelContext, cfg: dict,
                      eps_ratio: float | None = None) -> dict:
        """岛明细（data/islands.jsonl 的行）：聚类结果 + 每岛的规模与跨岛距离。"""
        comps = ctx.components
        n = len(comps)
        diag = ctx.diag
        parts_cfg = cfg["parts"]
        ratio = float(parts_cfg["island_eps_ratio"] if eps_ratio is None
                      else eps_ratio)
        eps = ratio * diag
        report: dict[str, Any] = {"key": ctx.key, "diag": round(diag, 6),
                                  "eps_ratio": ratio, "n_comps": n,
                                  "n_islands": 0, "islands": []}
        if n == 0:
            return report

        total_faces = sum(int(len(c.faces)) for c in comps)
        vertex_cap = int(parts_cfg["island_query_points"])
        surface_count = int(parts_cfg["island_surface_samples"])
        need_idx = n > 1
        indices = [_SurfIndex(c) for c in comps] if need_idx else []
        pair = precise_pair_distances(comps, eps, vertex_cap, surface_count,
                                      indices=indices)
        bbox_gap = bbox_gap_matrix(comps)
        groups = cluster_islands(n, pair, eps)

        need_qp = any(len(g) < n for g in groups) and n > 1
        qp = [query_points(c, vertex_cap, surface_count) for c in comps] \
            if need_qp else []
        main_rank = ctx.main_rank

        for k, ranks in enumerate(groups):
            is_main = main_rank in ranks
            others = [i for g in groups if g is not ranks for i in g]
            gap = _min_cross_gap(ranks, others, pair, bbox_gap, indices, qp) \
                if others else None
            faces = sum(int(len(comps[i].faces)) for i in ranks)
            report["islands"].append({
                "id": k, "is_main": is_main, "floating": not is_main,
                "ranks": ranks, "shards": len(ranks), "faces": faces,
                "share": round(faces / total_faces, 5) if total_faces else 0.0,
                "gap_ratio": (round(gap / diag, 5) if (gap is not None and diag > 0)
                              else None),
            })
        report["n_islands"] = len(groups)
        return report


# 检测器注册表（骨架的全部"插拔"机制）：顺序即合并冲突时的次级依据，
# 缺陷类优先级由 findings.merge 按类处理
REGISTRY: list = [CoLocatedDetector(), FloatingIslandDetector()]
