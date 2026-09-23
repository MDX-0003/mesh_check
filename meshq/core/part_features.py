"""§4 部件特征提取器：把每个非主组件变成一行可比较的数字（**本模块不做任何判定**）。

判定在 part_verdict.py；本模块只产出特征表 data/parts.jsonl（一行 = 一个模型）。

## 三条判据轴（PLAN-03 §5.1）

| 轴 | 字段 | 用途 |
|---|---|---|
| 疏离度 | `gap_to_main`、`gap_to_nearest_other`、`nearest_rank` | 识别"位移型"伪影 |
| 形态 | `n_faces`、`volume`、`bbox_thinness`、`fill_ratio`、`watertight`、`euler`、`boundary_edges`、`non_manifold_edges` | 识别"碎屑/薄片"型伪影 |
| 群体 | `cluster_size`、`siblings`（尺寸轴一维聚类） | A 档独立性校验 / C 档整组位移拦截 |

A0 真实性门所需的两条也在这里：`bbox_pinned`（贴模型包围盒平面的退化片）与
`engine_seen_by_bpy`（双引擎划分一致性）。实测依据：p01@smart-topology 的 3 个"2 面件"
bbox 的 Y 恰好是 ±0.5、面积 2.9e-06~3.5e-05，且只在 trimesh 侧存在（bpy 1e-4 下被并走）。

## 两条必须知道的口径

1. **邻近量是"顶点采样"距离，不是精确面距。** 用 scipy cKDTree 在组件顶点上算，省掉三角树
   构建；偏差约为一个顶点间距。对"疏离/贴合"这种量级差异（实测 30 倍以上）无影响，
   但引用绝对数字时必须带上这一条（PLAN-03 §1.3 的实测用的是精确面距）。
2. **两个 gap 的分工**：`gap_to_main` 到主组件；`gap_to_nearest_other` 到**任何**其它组件。
   `iso` 轴取后者（主组件也是"其它组件"之一）。PLAN-03 §1.3 的实测只测过 `gap_to_main`。

改本模块必改 tests/test_part_features.py。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from meshq.core.common import DATA_DIR, load_config, read_jsonl_by_key
from meshq.core.geometry import bounds_stats, classify_pieces, main_piece_index
from meshq.core.mesh_ops import (bbox_overlap_ratio, bbox_pinned_piece, edge_classes,
                      sample_points, split_components, weld)

PARTS_FILE = DATA_DIR / "parts.jsonl"

# 面数匹配容差。**面数不是可靠的匹配键**：bpy 的 remove_doubles(1e-4) 是邻近合并，会把稠密
# 网格上的近退化面塌掉——实测 p12@standard 主组件 644,972（trimesh）→ 644,374（bpy），差 598 面；
# 而 diag 两侧一致到小数点后 6 位。故面数只作弱校验（绝对下限 + 相对比例），**主键是 diag**。
ENGINE_FACE_TOL = 2
ENGINE_FACE_REL_TOL = 0.01
ENGINE_DIAG_TOL = 0.02   # diag 相对差容差（实测对应件的差为 0）


def size_clusters(diags: list[float], ratio: float) -> list[int]:
    """按 bbox 对角线做一维聚类，返回每个元素所在簇的成员数（含自己）。

    与**当前簇首元素**比较，超过 ratio 就开新簇——以簇首而非相邻元素比较，避免链式合并
    把整个尺寸区间连成一簇。固定百分比容差已被实测证伪：`±15%` 下 p01@smart 的 0.7301 与
    0.8402 相差 15.07%，恰好落在容差外，导致同一对腿的"兄弟数"算成 1（PLAN-03 §5.3）。
    """
    if not diags:
        return []
    order = sorted(range(len(diags)), key=lambda i: diags[i])
    cluster_size = [1] * len(diags)
    cur: list[int] = []
    for i in order:
        head = diags[cur[0]] if cur else None
        if head is None or diags[i] <= head * ratio or diags[i] <= 0:
            cur.append(i)
        else:
            for j in cur:
                cluster_size[j] = len(cur)
            cur = [i]
    for j in cur:
        cluster_size[j] = len(cur)
    return cluster_size


def pair_distances(samples: list[np.ndarray], vertices: list[np.ndarray],
                   contact_tol: float) -> list[dict[int, dict[str, float]]]:
    """组件两两之间的"顶点采样"距离：最小距离与接触点占比。

    返回 `D[i][j] = {"min": 最小距离, "frac": i 的采样点中距 j 在 contact_tol 内的占比}`（i≠j）。
    一次算全矩阵，供 `gap_to_main` / `gap_to_nearest_other` / `contact_frac` 共用；
    逐件单独建树会让同一对组件被重复查询。
    """
    trees = [cKDTree(v) if len(v) else None for v in vertices]
    out: list[dict[int, dict[str, float]]] = [dict() for _ in samples]
    for i, pts in enumerate(samples):
        if len(pts) == 0:
            continue
        for j, tree in enumerate(trees):
            if i == j or tree is None:
                continue
            dist, _ = tree.query(pts, k=1)
            out[i][j] = {"min": float(np.min(dist)),
                         "frac": float(np.mean(dist <= contact_tol))}
    return out


def match_bpy_partition(pieces: list[dict], bpy_pieces: list[dict],
                        diag_tol: float = ENGINE_DIAG_TOL) -> list[int | None]:
    """每个 trimesh 组件在 bpy 划分里的对应件下标（A0 真实性门：双引擎一致性）。

    匹配判据：存在 bpy 组件，**diag 相对差** <= diag_tol **且** 面数差在弱容差内
    （`max(ENGINE_FACE_TOL, ENGINE_FACE_REL_TOL × 面数)`）。

    **为什么比绝对 diag 而不是"各自除以主组件对角线"**：两个引擎可能对"哪个件最大"给出不同
    答案——p01@smart-topology 的 816 面件在 trimesh 侧比值 1.0、在 bpy 侧 0.772，因为真正的
    bbox 最大件是那只 599/598 面的。一旦用各自的主做归一，两边的比值就不可比（这个坑由单测
    抓出）。绝对 diag 直接可比：两引擎都在原始 GLB 坐标系下算 bbox，实测对应件完全一致。

    **为什么面数只能是弱校验**：bpy 焊接会塌掉稠密网格的近退化面（p12 差 598 面），
    而 p01@smart 的 2 面片在 bpy 侧最近的件是 301 面——相对容差刚好把前者放过、把后者拦住。
    """
    if not bpy_pieces or not pieces:
        return [None] * len(pieces)
    matched: list[int | None] = []
    for p in pieces:
        d = float(p.get("diag") or 0.0)
        nf = int(p["n_faces"])
        hit = None
        for bi, b in enumerate(bpy_pieces):
            bd = float(b.get("diag") or 0.0)
            bfaces = int(b.get("n_faces") or 0)
            denom = max(d, bd)
            rel = (abs(d - bd) / denom) if denom > 0 else 0.0
            face_tol = max(ENGINE_FACE_TOL, int(ENGINE_FACE_REL_TOL * max(nf, bfaces)))
            if rel <= diag_tol and abs(bfaces - nf) <= face_tol:
                hit = bi
                break
        matched.append(hit)
    return matched


def bpy_pieces_for(raw_dir: Path) -> list[dict]:
    """取 bpy 侧组件划分：唯一来源 `blender_checks.json`。

    原先还回退 `render/parts_legend.json`——那份产物随"每件一色"一起撤销
    （PLAN-07 §五），且 `blender_checks.json` 本就由每次渲染写出、分量字段齐全，
    回退路径是冗余的。
    """
    checks = raw_dir / "blender_checks.json"
    if checks.is_file():
        try:
            data = json.loads(checks.read_text(encoding="utf-8"))
            if data.get("pieces"):
                return list(data["pieces"])
        except json.JSONDecodeError:
            pass
    return []


def extract(glb_path: Path, parts_cfg: dict, bpy_pieces: list[dict] | None = None,
            diag_ratio: float = 0.10) -> dict:
    """单个 GLB → 特征表记录（模型级 + 每个组件一行）。"""
    mesh = weld(trimesh.load(glb_path, force="mesh"))
    comps = split_components(mesh)

    pieces = [{"diag": bounds_stats(*c.bounds)["diag"], "n_faces": int(len(c.faces))}
              for c in comps]
    classify_pieces(pieces, diag_ratio)          # 填 diag_ratio_to_main / is_small
    main_i = main_piece_index(pieces) if pieces else 0
    main_diag = pieces[main_i]["diag"] if pieces else 0.0

    cluster_tol = float(parts_cfg["sibling_cluster_ratio"])
    cluster_size = size_clusters([pieces[i]["diag"] for i in range(len(pieces))],
                                 cluster_tol)
    main_volume = bounds_stats(*comps[main_i].bounds)["volume"] if comps else 0.0
    bpy_pieces = bpy_pieces or []
    engine_match = match_bpy_partition(pieces, bpy_pieces)
    engine_seen = [m is not None for m in engine_match]

    samples = [sample_points(np.asarray(c.vertices), int(parts_cfg["sample_points"]))
               for c in comps]
    vertices = [np.asarray(c.vertices) for c in comps]
    contact_tol = float(parts_cfg["contact_tol"]) * main_diag
    D = pair_distances(samples, vertices, contact_tol)

    model_bounds = mesh.bounds
    pin_tol = float(parts_cfg["bbox_pin_tol"]) * main_diag
    out_parts = []
    for i, comp in enumerate(comps):
        stats = bounds_stats(*comp.bounds)
        ec = edge_classes(comp)
        others = D[i]
        gap_main = others.get(main_i, {}).get("min")
        nearest_rank, nearest_dist = None, None
        for j, rec in others.items():
            if nearest_dist is None or rec["min"] < nearest_dist:
                nearest_rank, nearest_dist = j, rec["min"]
        contact_frac = max((r["frac"] for r in others.values()), default=0.0)
        extents = np.asarray(comp.extents, dtype=float)
        emax = float(np.max(extents)) if len(extents) else 0.0
        area = float(comp.area)
        out_parts.append({
            "rank": i,
            "n_faces": int(len(comp.faces)),
            "n_verts": int(len(comp.vertices)),
            "diag": stats["diag"],
            "diag_ratio": (stats["diag"] / main_diag) if main_diag > 0 else None,
            "volume": stats["volume"],
            "vol_ratio": ((stats["volume"] / main_volume) if main_volume > 0 else None),
            "diag_ratio_to_main": pieces[i]["diag_ratio_to_main"],
            "is_small_default": pieces[i]["is_small"],
            "is_main": i == main_i,
            # 形态
            "watertight": bool(comp.is_watertight),
            "euler": int(comp.euler_number),
            "boundary_edges": ec["boundary_edges"],
            "non_manifold_edges": ec["non_manifold_edges"],
            "bbox_thinness": (float(np.min(extents)) / emax) if emax > 0 else 0.0,
            "fill_ratio": (stats["volume"] / (area ** 1.5)) if area > 0 else None,
            # 疏离度（顶点采样距离；None = 只有一个组件、无处可比）
            "gap_to_main": gap_main,
            "gap_to_nearest_other": nearest_dist,
            "nearest_rank": nearest_rank,
            "gap_ratio": (nearest_dist / main_diag) if (nearest_dist is not None
                                                        and main_diag > 0) else None,
            "contact_frac": contact_frac,
            "bbox_overlap_main": (bbox_overlap_ratio(comp.bounds, comps[main_i].bounds)
                                  if len(comps) > 1 else 0.0),
            # 群体
            "cluster_size": cluster_size[i] if i < len(cluster_size) else 1,
            "siblings": (cluster_size[i] - 1) if i < len(cluster_size) else 0,
            # A0 真实性门的两条
            "bbox_pinned": bbox_pinned_piece(comp.bounds, model_bounds, pin_tol),
            "engine_seen_by_bpy": engine_seen[i] if i < len(engine_seen) else False,
            "engine_bpy_rank": engine_match[i] if i < len(engine_match) else None,
        })

    model_ec = edge_classes(mesh)
    return {
        "key": glb_path.parent.name,
        "source": glb_path.name,
        "engine": "trimesh",
        "weld_digits": int(parts_cfg.get("weld_digits", 5)),
        "n_faces": int(len(mesh.faces)),
        "n_verts": int(len(mesh.vertices)),
        "n_pieces": len(comps),
        "boundary_edges": model_ec["boundary_edges"],
        "non_manifold_edges": model_ec["non_manifold_edges"],
        "main_rank": main_i,
        "main_diag": main_diag,
        "bpy_n_pieces": len(bpy_pieces) or None,
        "engine_partition_match": sum(engine_seen),
        "parts": out_parts,
    }


def iter_glbs(data_dir: Path = DATA_DIR, keys: list[str] | None = None,
              fixed: bool = False):
    root = data_dir / ("fixed" if fixed else "raw")
    if not root.exists():
        return
    for d in sorted(root.iterdir()):
        if keys and d.name not in keys:
            continue
        glb = (d / "model_fixed.glb") if fixed else (d / "model.glb")
        if glb.is_file():
            yield d.name, glb


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="组件特征提取 → parts.jsonl（不做判定）")
    ap.add_argument("--keys", nargs="*")
    ap.add_argument("--fixed", action="store_true", help="读 data/fixed（model_fixed.glb）")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config()
    parts_cfg = cfg["parts"]
    diag_ratio = float(cfg["detect"]["diag_ratio"])

    existing: dict[str, dict] = {}
    explicit = set(args.keys or [])
    # 安全线：--keys + --force = 只强制重算列出的键，其余记录一律保留；
    # 只有不带 --keys 的全量 --force 才允许丢掉旧表从零重建
    if not (args.force and not explicit):
        existing = read_jsonl_by_key(PARTS_FILE)

    todo = [(k, g) for k, g in iter_glbs(DATA_DIR, args.keys, args.fixed)
            if k not in existing or args.force or k in explicit]
    todo_set = {k for k, _ in todo}
    out_lines = [json.dumps(existing[k], ensure_ascii=False)
                 for k in existing if k not in todo_set]
    for key, glb in todo:
        rec = extract(glb, parts_cfg, bpy_pieces_for(glb.parent), diag_ratio)
        out_lines.append(json.dumps(rec, ensure_ascii=False))
        print(f"[parts] {key} pieces={rec['n_pieces']} "
              f"(bpy {rec['bpy_n_pieces']}) 双侧可见 {rec['engine_partition_match']} "
              f"boundary={rec['boundary_edges']} nm={rec['non_manifold_edges']}")
    PARTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PARTS_FILE.write_text("\n".join(out_lines) + "\n", encoding="utf-8", newline="\n")
    print(f"parts.jsonl: {len(todo)} 新增 / {len(out_lines)} 总计")
    return 0


if __name__ == "__main__":
    sys.exit(main())
