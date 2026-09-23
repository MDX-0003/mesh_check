"""定位图的**视角选择**纯逻辑（PLAN-07 §3.3）。

不依赖 bpy / Pillow：编排器侧用 trimesh 载入单件网格，在本模块里选出"把这一处重叠看得
最清"的机位方向，方向以**单位向量**跨进程传给 Blender。为什么用方向而不是机位矩阵：
方向是纯几何量，两侧无需共享面序或相机约定，也不怕 glTF 导入顺序差异。

## 为什么不需要可见性测量（PLAN-07 §3.1/§3.3 的关键结论）

定位图里除被强调的两件外**全部走半透明灰底**，不透明件不再遮挡组件——遮挡因此不是一个
"需要测量并兜底"的约束，而直接消失了。于是选视角只需回答"哪个角度把这一处重叠看得最清"，
退化为纯投影运算。

## 打分口径

- `area(d)`：A、B 两件**投影后的三角形面积之和**（"两片占多大画幅"）。
- `short(d)`：A、B 各自投影包围盒**短边的较小者**（专治薄片：正对时最大、侧看趋 0，
  有它就不会选出"斜着一看是一条线"的机位）。
- `pref(d)`：朝向偏好，避免贴地俯视与极端仰角。

三项各自归一化后加权求和。**标准视图优先条款**（评审意见 §九-4）：4 个标准视图里若有
任一个达到最优分的 `canonical_ratio`（默认 0.9）以上，就选它——保证机位基本落在读者熟悉
的位置，只有标准视图明显更差时才用偏角。
"""

from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np

# 与 lookdev_math.VIEWS 的机位一致（取其方向）：读者熟悉的标准视图
CANONICAL_VIEWS = ("front", "top", "right", "iso")

DEFAULTS: dict[str, Any] = {
    "n_directions": 64,          # Fibonacci 球面候选数（确定性，无 RNG）
    "w_area": 1.0,
    "w_short": 1.5,              # 短边项略重于面积项，"治薄片"是它的专职
    "w_pref": 0.15,
    "canonical_ratio": 0.9,      # 标准视图优先：达到最优分此比例即改用标准视图
    "min_elevation_deg": -25.0,  # 低于此俯仰的方向重罚（钻到地面以下）
}


def fibonacci_directions(n: int) -> list[np.ndarray]:
    """球面上 n 个近似均匀的单位方向（黄金角螺旋，确定性、无随机数）。

    与仓库"可复现、可跨次比对"的约定一致（同 `detectors.surface_samples` 的理由）。
    """
    if n <= 0:
        return []
    out = []
    golden = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(n):
        z = 1.0 - 2.0 * (i + 0.5) / n
        r = math.sqrt(max(0.0, 1.0 - z * z))
        theta = golden * i
        out.append(np.array([r * math.cos(theta), r * math.sin(theta), z]))
    return out


def canonical_directions(views: Iterable[tuple[float, float, float]]
                         ) -> list[tuple[str, np.ndarray]]:
    """标准机位坐标 → (名字, 单位方向)。机位坐标即"从原点看向机位的方向"。"""
    out = []
    for name, loc in views:
        v = np.asarray(loc, dtype=float)
        norm = float(np.linalg.norm(v))
        if norm > 0:
            out.append((name, v / norm))
    return out


def _basis(direction: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """给方向定一组正交基（投影平面）。用世界 Z 做参考轴，退化时换 Y。"""
    d = direction / (float(np.linalg.norm(direction)) or 1.0)
    ref = np.array([0.0, 0.0, 1.0])
    if abs(float(np.dot(d, ref))) > 0.99:
        ref = np.array([0.0, 1.0, 0.0])
    u = np.cross(ref, d)
    u = u / (float(np.linalg.norm(u)) or 1.0)
    v = np.cross(d, u)
    return u, v


def project(points: np.ndarray, direction: np.ndarray) -> np.ndarray:
    """三维点 → 垂直于 direction 的平面上的二维坐标（等距投影，够用且确定性）。"""
    u, v = _basis(direction)
    return np.stack([points @ u, points @ v], axis=1)


def _projected_triangle_area(tris: np.ndarray, direction: np.ndarray) -> float:
    """三角形集合在给定方向下的投影面积之和（鞋带公式；三角形退化为线段时为 0）。"""
    if len(tris) == 0:
        return 0.0
    p = project(np.asarray(tris, dtype=float).reshape(-1, 3), direction)
    p = p.reshape(-1, 3, 2)
    x, y = p[:, :, 0], p[:, :, 1]
    return float(np.abs((x[:, 1] - x[:, 0]) * (y[:, 2] - y[:, 0])
                        - (x[:, 2] - x[:, 0]) * (y[:, 1] - y[:, 0])).sum() / 2.0)


def _projected_bbox_short(points: np.ndarray, direction: np.ndarray) -> float:
    """投影包围盒的短边（薄片正对时最大、侧看趋 0）。"""
    if len(points) == 0:
        return 0.0
    p = project(np.asarray(points, dtype=float), direction)
    ext = p.max(axis=0) - p.min(axis=0)
    return float(min(ext[0], ext[1]))


def _elevation_penalty(direction: np.ndarray, min_elev_deg: float) -> float:
    """俯仰惩罚：方向越低（越贴地/越仰视）罚得越重；限值以上不罚。"""
    d = direction / (float(np.linalg.norm(direction)) or 1.0)
    elev = math.degrees(math.asin(max(-1.0, min(1.0, float(d[2])))))
    if elev >= min_elev_deg:
        return 0.0
    return min(1.0, (min_elev_deg - elev) / 90.0)


def score_direction(direction: np.ndarray, tris: list[np.ndarray],
                    points: list[np.ndarray], diag: float,
                    cfg: dict[str, Any] | None = None) -> dict[str, float]:
    """单方向的分数与其分项（各项按 diag 归一化，权重才有可比的量纲）。"""
    c = {**DEFAULTS, **(cfg or {})}
    scale = diag * diag if diag > 0 else 1.0
    area = sum(_projected_triangle_area(t, direction) for t in tris) / scale
    short = min((_projected_bbox_short(p, direction) for p in points),
                default=0.0) / (diag if diag > 0 else 1.0)
    pref = 1.0 - _elevation_penalty(direction, float(c["min_elevation_deg"]))
    return {"area": area, "short": short, "pref": pref,
            "score": float(c["w_area"]) * area + float(c["w_short"]) * short
                     + float(c["w_pref"]) * pref}


def choose_view(tris: list[np.ndarray], points: list[np.ndarray], diag: float,
                canonical: list[tuple[str, np.ndarray]] | None = None,
                cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """选出"重叠看得最清"的机位方向。

    `tris` / `points`：参与评分的各件（[该组件, 邻居]，无邻居时只给一件）。
    返回 `{direction, score, view(标准视图名或 "auto"), breakdown}`。
    """
    c = {**DEFAULTS, **(cfg or {})}
    if not tris or not points or diag <= 0:
        return {"direction": [0.0, -1.0, 0.3], "score": 0.0, "view": "auto",
                "breakdown": {}}
    scored: list[tuple[list[str], np.ndarray, dict[str, float]]] = []
    for name, d in (canonical or []):
        scored.append((["canonical", name], d, score_direction(d, tris, points, diag, c)))
    for d in fibonacci_directions(int(c["n_directions"])):
        scored.append((["auto"], d, score_direction(d, tris, points, diag, c)))
    best = max(scored, key=lambda s: s[2]["score"])
    # 标准视图优先条款：标准视图够好就用它（机位落在读者熟悉的位置）
    canon = [s for s in scored if s[0][0] == "canonical"]
    if canon:
        pick = max(canon, key=lambda s: s[2]["score"])
        if pick[2]["score"] >= float(c["canonical_ratio"]) * best[2]["score"]:
            best = pick
    view = best[0][1] if best[0][0] == "canonical" else "auto"
    return {"direction": [float(x) for x in best[1]], "score": best[2]["score"],
            "view": view, "breakdown": best[2]}
