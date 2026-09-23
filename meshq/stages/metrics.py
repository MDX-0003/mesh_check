"""03 · trimesh 独立复核指标（第二引擎，与 bpy 原生检查交叉验证）。

读 data/raw/<key>/model.glb → 写 data/metrics.jsonl（每模型一条自描述记录）。

口径（PLAN-03 §3，与 bpy 侧共用 geometry.py 的定义）：
- `non_manifold_edges` 已收窄为**真非流形**（相邻面计数>2）；开放边界（计数=1）另立
  `boundary_edges`。历史记录把两者合计，故旧 metrics.jsonl 的该字段口径与现在不同
  （standard 批次已冻结、不重算，引用旧数字时必须标注口径版本）。
- `pieces` 仍按面数降序、仍只带 4 个字段（n_verts/n_faces/diag/volume），保持本产物的瘦身契约；
  判别所需的完整组件特征（封闭性/欧拉数/边分类/邻近关系）在 part_features.py 的 parts.jsonl。

焊接、连通域、边分类的实现都在 mesh_ops.py（与 detect / part_features 共用同一份，勿在此重复实现）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import trimesh

from meshq.core.common import DATA_DIR, read_jsonl_by_key
from meshq.core.geometry import classify_pieces
from meshq.core.mesh_ops import WELD_DIGITS, edge_classes, split_components, weld  # noqa: F401

METRICS_FILE = DATA_DIR / "metrics.jsonl"


def piece_records(mesh: trimesh.Trimesh) -> list[dict]:
    """连通域统计（供小碎片分类），按面数降序；只带 4 个字段（瘦身契约）。"""
    pieces = []
    for comp in split_components(mesh):
        bmin, bmax = comp.bounds
        pieces.append({
            "n_verts": int(len(comp.vertices)),
            "n_faces": int(len(comp.faces)),
            "diag": float(np.linalg.norm(bmax - bmin)),
            "volume": float(np.prod(bmax - bmin)),
        })
    return pieces


def inspect_glb(glb_path: Path) -> dict:
    raw = trimesh.load(glb_path, force="mesh")
    raw_bbox = {"min": [float(x) for x in raw.bounds[0]],
                "max": [float(x) for x in raw.bounds[1]],
                "extents": [float(x) for x in raw.extents]}
    m = weld(raw)

    pieces = piece_records(m)
    classify_pieces(pieces, diag_ratio=0.10)  # 展示用默认；判定阈值以 config 为准（04）
    small_faces = sum(p["n_faces"] for p in pieces if p["is_small"])
    total_faces = int(len(m.faces))

    ec = edge_classes(m)

    return {
        "engine": "trimesh",
        "n_verts_raw": int(len(raw.vertices)),
        "n_verts": int(len(m.vertices)),
        "n_faces": total_faces,
        "watertight": bool(m.is_watertight),
        "winding_consistent": bool(m.is_winding_consistent),
        "euler_number": int(m.euler_number),
        "boundary_edges": ec["boundary_edges"],
        "non_manifold_edges": ec["non_manifold_edges"],
        "degenerate_faces": int((~m.nondegenerate_faces(height=1e-8)).sum()),
        "n_pieces": len(pieces),
        "n_small_pieces_default": sum(1 for p in pieces if p["is_small"]),
        "fragment_face_ratio_default": round(small_faces / total_faces, 6) if total_faces else 0.0,
        "pieces": pieces,  # 全量保留：截断会让排名靠后的小组件逃过分类（memory 2026-09-21）
        "raw_bbox": raw_bbox,
        "source": glb_path.name,
    }


def iter_model_keys(data_dir: Path = DATA_DIR, keys: list[str] | None = None):
    raw = data_dir / "raw"
    if not raw.exists():
        return
    for d in sorted(raw.iterdir()):
        if keys and d.name not in keys:
            continue
        if (d / "model.glb").is_file():
            yield d.name, d / "model.glb"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="trimesh 指标复核 → metrics.jsonl")
    ap.add_argument("--keys", nargs="*")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict] = {}
    explicit = set(args.keys or [])
    # 安全线：--keys + --force = 只强制重算列出的键，其余记录一律保留；
    # 只有不带 --keys 的全量 --force 才允许丢掉旧表从零重建
    if not (args.force and not explicit):
        existing = read_jsonl_by_key(METRICS_FILE)

    # DATA_DIR 在调用时引用（而非默认参数绑定），便于测试注入
    todo: list[tuple[str, Path]] = []
    for key, glb in iter_model_keys(data_dir=DATA_DIR, keys=args.keys):
        if key in existing and not args.force and key not in explicit:
            print(f"[skip] {key} 已有记录")
            continue
        todo.append((key, glb))

    out_lines = [json.dumps(existing[k], ensure_ascii=False) for k in existing
                 if k not in {t[0] for t in todo}]
    n_new = 0
    for key, glb in todo:
        rec = {"key": key, **inspect_glb(glb)}
        out_lines.append(json.dumps(rec, ensure_ascii=False))
        n_new += 1
        print(f"[inspect] {key} faces={rec['n_faces']} pieces={rec['n_pieces']} "
              f"boundary={rec['boundary_edges']} nm={rec['non_manifold_edges']}")
    METRICS_FILE.write_text("\n".join(out_lines) + "\n", encoding="utf-8", newline="\n")
    print(f"metrics.jsonl: {n_new} 新增 / {len(out_lines)} 总计")
    return 0


if __name__ == "__main__":
    sys.exit(main())
