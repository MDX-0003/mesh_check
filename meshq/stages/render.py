"""02 · Blender headless 渲染编排器。

对 data/raw/<key>/model.glb 逐个调起 render_one.py（bpy 检查 + lookdev 三视图三通道渲染
+ iso 总览），渲染成功后做后处理（PLAN-02）：线框 SSAA 降回 →（修复件才有）平滑|线框
对比图。渲染目标按图种可单独重跑（`--target overview|highlight|wire|pieces|locator|all`）。

  uv run python -m meshq.stages.render                # 处理 data/raw 下全部未处理模型
  uv run python -m meshq.stages.render --keys p01@standard
  uv run python -m meshq.stages.render --force        # 已处理也重跑
  uv run python -m meshq.stages.render --clean-old    # 按白名单清理非现役残留图后退出
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from meshq.core.common import (DATA_DIR, ROOT, ConfigError, get_blender_exe,
                                load_config, read_jsonl)
from meshq.core.lookdev_math import PIECE_VIEWS

RENDER_ONE = ROOT / "meshq" / "blender" / "render_one.py"   # Blender 侧入口（见 meshq/blender/__init__.py）
GRID = ("front", "top", "right")

# 清理白名单：现役产物名族的**唯一来源**。加新图种必须在这里登记，否则
# `--clean-old` 会把它当旧残留删掉——2026-09-23 实测：名单落在手写常量后面时，
# 一次 run 删掉了 1345 张 piece_/locator_ 图（当时最新、正被交付页消费）。
_VIEW_ALT = "|".join(GRID)
PRODUCT_PNG_PATTERNS = tuple(re.compile(p) for p in (
    rf"base_({_VIEW_ALT}|iso)\.png",
    rf"highlight_({_VIEW_ALT})\.png",
    rf"wire_({_VIEW_ALT})\.png",
    r"compare_front\.png",
    rf"piece_\d+_({'|'.join(PIECE_VIEWS)})\.png",
    r"locator_\d+_(model|close)\.png",
))


def is_current_product(name: str) -> bool:
    """该 png 是否属于现役产物集（`clean_legacy` 的判据）。"""
    return any(p.fullmatch(name) for p in PRODUCT_PNG_PATTERNS)



def clean_stale_piece_views(render_dir: Path) -> list[str]:
    """删除非当前命名（`lookdev_math.PIECE_VIEWS`）的单件残留图，返回被删文件名。

    2026-09-23：单件机位由旧的固定 front/top/right 改为贴合机位 face/edge/third 后，
    旧命名的图从未被清理，而交付层当时用 `piece_<rank>_*.png` 通配取图，
    于是条带被卷进 6 张（3 张新 + 3 张陈旧）。交付层已改为按 PIECE_VIEWS 精确取图，
    这里再清一次目录，避免产物本身说谎。
    """
    removed: list[str] = []
    for f in sorted(render_dir.glob("piece_*_*.png")):
        if f.stem.split("_")[-1] not in PIECE_VIEWS:
            f.unlink()
            removed.append(f.name)
    return removed


def build_cmd(blender_exe: Path, glb: Path, out_dir: Path, checks_path: Path,
              res: tuple[int, int], diag_ratio: float,
              lookdev: dict | None = None,
              parts: dict | None = None) -> list[str]:
    # --python-exit-code：脚本内 Python 异常必须以非零退出码上抛。Blender 默认吞掉
    # 异常返回 0，曾把通道崩溃伪装成成功（[ok] 后产物仍是旧的），排查走了弯路。
    cmd = [
        str(blender_exe), "--background", "--python-exit-code", "1",
        "--python", str(RENDER_ONE), "--",
        str(glb), str(out_dir), str(checks_path),
        str(res[0]), str(res[1]), f"{diag_ratio:g}",
        json.dumps(lookdev or {}, ensure_ascii=False),
    ]
    # parts 缺省不传 ⇒ render_one 只渲染既有三通道（对既有产物零影响）
    if parts:
        cmd.append(json.dumps(parts, ensure_ascii=False))
    return cmd


def find_models(data_dir: Path = DATA_DIR) -> list[str]:
    raw = data_dir / "raw"
    if not raw.exists():
        return []
    return sorted(d.name for d in raw.iterdir() if (d / "model.glb").is_file())


def expected_outputs(render_dir: Path) -> list[Path]:
    """三通道（base/highlight/wire）的预期产物。

    第四通道"组件配色诊断"与 `highlight_regions.json`/`review_sheet.png` 已随
    PLAN-07 §五 撤销（每件一色在组件多时物理上不成立；定位改为逐检出定位图），
    故不再参与齐全判定。
    """
    out = [render_dir / "base_iso.png"]
    for v in GRID:
        out += [render_dir / f"base_{v}.png",
                render_dir / f"highlight_{v}.png",
                render_dir / f"wire_{v}.png"]
    return out


def outputs_complete(raw_dir: Path) -> bool:
    """三通道产物齐全判定（键在 blender_checks.json + expected_outputs）。"""
    rd = raw_dir / "render"
    if not (raw_dir / "blender_checks.json").is_file():
        return False
    return all(p.is_file() for p in expected_outputs(rd))


def hex_rgb(h: str) -> tuple[int, int, int]:
    """#RRGGBB → 8bit RGB（画布底色用，不做线性转换）。实现见 lookdev_math。"""
    from meshq.core.lookdev_math import hex_to_rgb
    return hex_to_rgb(h)


def stitch_compare(left: Path, right: Path, dest: Path,
                   bg: tuple[int, int, int] = (60, 64, 72)) -> None:
    """左右拼接为一张对比图（左右语义由调用方决定），接缝底色与场景背景一致。

    修复前后对比（可读性修复 2026-09-21）：两侧均为线框渲染——平滑着色看不出
    几何差异，线框才能把"删掉了什么"摆到明面上。
    """
    from PIL import Image

    ia, ib = Image.open(left).convert("RGB"), Image.open(right).convert("RGB")
    h = min(ia.height, ib.height)
    gap = 8
    canvas = Image.new("RGB", (ia.width + gap + ib.width, h), bg)
    canvas.paste(ia, (0, 0))
    canvas.paste(ib, (ia.width + gap, 0))
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest)


RENDER_TARGETS = ("overview", "highlight", "wire", "pieces", "locator", "all")

# 渲染目标 → 通道（render_one 按此裁剪工作范围）；pieces 目标 = 只跑单件隔离渲染
TARGET_CHANNELS = {
    "overview": ["base"],
    "highlight": ["highlight"],
    "wire": ["wire"],
    "pieces": [],
    "locator": [],
    "all": ["base", "highlight", "wire"],
}

# 渲染目标 → 完整性产物（图种级跳过判定；pieces 是显式意图，永远重跑）
TARGET_OUTPUTS = {
    "overview": ["base_front.png", "base_iso.png"],
    "highlight": ["highlight_front.png"],
    "wire": ["wire_front.png"],
    "pieces": [],
    "locator": ["locator_marks.json"],
    "all": ["base_front.png", "base_iso.png", "highlight_front.png",
            "wire_front.png", "blender_checks.json"],
}


def target_channels(target: str) -> list[str]:
    return list(TARGET_CHANNELS[target])


def target_complete(raw_dir: Path, target: str) -> bool:
    """图种级完整性：该目标的标志性产物齐备即视为完成。

    pieces 是例外：单件清单随 findings 变化，且 `--target pieces` 本身就是
    "重跑图证"的显式意图——视为永不完成，总是重跑。
    """
    if target == "pieces":
        return False
    render = raw_dir / "render"
    return all((render / f).is_file()
               for f in TARGET_OUTPUTS.get(target, []))


def resolve_target(args) -> str:
    """新旗标优先，旧旗标映射为等价目标（向后兼容）。"""
    if getattr(args, "target", None):
        return args.target
    if getattr(args, "pieces_only", False):
        return "pieces"
    return "all"


def export_review_pieces(key: str, out_dir: Path) -> list[Path]:
    """把待复核检出的组件（trimesh 划分口径）导出为独立 GLB，供 Blender 单件
    隔离渲染（PLAN-05 图证补齐）。

    为什么必须单独导出：仅单引擎可见的组件在 bpy 的 1e-4 焊接划分里已并入本体
    （顶点重合被焊接），主场景渲染中没有属于它的独立像素框可裁；而它在 trimesh
    划分下永远是一个独立组件。组件顶点本就在模型坐标系，单件 GLB 直接对齐。
    findings 缺失或该模型无待复核检出 → 返回空表（不产单件渲染）。但 **piece store
    本身无条件构建**（PLAN-06 §2.4.1）：渲染端逐件导入、HTML 图例、单件图证都依赖它，
    且它与"有没有检出"无关——曾经因为只在有待复核件时才构建，导致三个"确定通过"的
    模型没有任何 pieces/ 产物，改造渲染端时会无件可导。
    """
    from meshq.core.findings import read_findings
    from meshq.core.piece_store import ensure_store, piece_glbs

    # 拆分产物化（PLAN-05 P1）：从 piece store 取单件 GLB，不再重复 weld+split。
    # weld_digits 的读取口径与 part_features 一致（缺段时回退 5，勿另立默认值）
    parts_cfg = load_config().get("parts") or {}
    manifest = ensure_store(key, DATA_DIR,
                            weld_digits=int(parts_cfg.get("weld_digits", 5)))
    findings_path = DATA_DIR / "findings.jsonl"
    if not findings_path.is_file():
        return []
    rows = [r for r in read_findings(findings_path)
            if r["key"] == key and r["tier"] == "review"]
    if not rows:
        return []
    glbs = piece_glbs(manifest, key, DATA_DIR)   # manifest 存相对路径 → 转绝对
    out = []
    seen = set()
    for r in rows:
        rank = str(int(r["rank"]))
        if rank in glbs and rank not in seen:
            out.append(Path(glbs[rank]))
            seen.add(rank)
    return out


def build_locator_items(key: str, raw_dir: Path) -> list[dict]:
    """待复核检出 → 定位图渲染清单（含视角选择）。

    视角由 `locator.choose_view` 纯投影算出（半透明灰底使遮挡不再是约束，
    故不需要任何可见性测量）；方向以单位向量随 argv 传进 Blender，两侧无面序耦合。
    """
    import numpy as np
    import trimesh

    from meshq.core.findings import read_findings
    from meshq.core.locator import CANONICAL_VIEWS, choose_view, canonical_directions
    from meshq.core.lookdev_math import VIEWS
    from meshq.core.piece_store import ensure_store, piece_glbs

    findings_path = DATA_DIR / "findings.jsonl"
    if not findings_path.is_file():
        return []
    rows = [r for r in read_findings(findings_path)
            if r["key"] == key and r["tier"] == "review"]
    if not rows:
        return []
    parts_cfg = load_config().get("parts") or {}
    manifest = ensure_store(key, DATA_DIR,
                            weld_digits=int(parts_cfg.get("weld_digits", 5)))
    glbs = piece_glbs(manifest, key, DATA_DIR)   # 相对路径 → 绝对（trimesh 与 argv 都要绝对）
    checks_path = raw_dir / "blender_checks.json"
    diag = 0.0
    if checks_path.is_file():
        nm = (json.loads(checks_path.read_text(encoding="utf-8")) or {}).get("normalize") or {}
        if nm.get("raw_bbox_min") and nm.get("raw_bbox_max"):
            diag = float(np.linalg.norm(np.asarray(nm["raw_bbox_max"], dtype=float)
                                        - np.asarray(nm["raw_bbox_min"], dtype=float)))
    canon = canonical_directions([(v, VIEWS[v]) for v in CANONICAL_VIEWS
                                  if v in VIEWS])
    meshes: dict[str, trimesh.Trimesh] = {}
    items = []
    for r in rows:
        rank = str(int(r["rank"]))
        if rank not in glbs:
            continue
        nb = (r.get("evidence") or {}).get("neighbor_rank")
        nb = str(int(nb)) if nb is not None and str(int(nb)) in glbs else None
        if rank not in meshes:
            meshes[rank] = trimesh.load(glbs[rank], force="mesh")
        a = meshes[rank]
        tris = [np.asarray(a.triangles, dtype=float)]
        pts = [np.asarray(a.vertices, dtype=float)]
        if nb:
            if nb not in meshes:
                meshes[nb] = trimesh.load(glbs[nb], force="mesh")
            b = meshes[nb]
            tris.append(np.asarray(b.triangles, dtype=float))
            pts.append(np.asarray(b.vertices, dtype=float))
        d = diag or float(max(a.extents))
        got = choose_view(tris, pts, d, canonical=canon)
        face_a = next((p["n_faces"] for p in manifest["pieces"]
                       if str(p["rank"]) == rank), None)
        face_b = next((p["n_faces"] for p in manifest["pieces"]
                       if str(p["rank"]) == nb), None) if nb else None
        items.append({"rank": int(r["rank"]), "neighbor_rank": nb and int(nb),
                      "expect_faces_a": face_a, "expect_faces_b": face_b,
                      # 路径在 JSON 边界一律转字符串（Blender 侧只认路径串）
                      "piece_glb": str(glbs[rank]),
                      "neighbor_glb": str(glbs[nb]) if nb else None,
                      "direction": got["direction"], "view": got["view"],
                      "score": got["score"],
                      # 重叠率用**面积比**（沿用检出侧的度量），不用面数比：
                      # 面数比在非均匀网格上与表格的百分比会自相矛盾（评审意见 §九-3）
                      "overlap_frac": (r.get("metrics") or {}).get("overlap_frac"),
                      "defect_class": r.get("defect_class")})
    return items


def build_tiers(key: str) -> dict[int, str]:
    """检出档位 → bpy rank 映射：{bpy_rank: "A"/"C"}（渲染高亮与 regions 的依据）。

    v2 口径（PLAN-04/05）：以 findings.jsonl 的**待复核检出**为准——悬浮 → 红
    （A 位），重叠共面 / 灰区 / 非核心 → 琥珀（C 位）；界面贴合（keep）与非缺陷
    不亮。同一 bpy rank 命中两类时取更重的 A。
    findings.jsonl 缺失或该模型无待复核检出时回退判别级联（part_verdicts A/C，
    standard 冻结口径）。

    判别/特征表的 rank 是 trimesh 侧口径，渲染端组件是 bpy 侧划分，经
    part_features 的双引擎逐组件匹配（engine_bpy_rank）转接；转接缺失的 rank
    放弃（不猜）。
    """
    model = load_jsonl_key(DATA_DIR / "parts.jsonl", key) or {}
    rank_to_bpy = {row.get("rank"): row.get("engine_bpy_rank")
                   for row in model.get("parts", [])}

    findings_path = DATA_DIR / "findings.jsonl"
    if findings_path.is_file():
        tiers: dict[int, str] = {}
        for f in read_jsonl(findings_path):
            if f.get("key") != key or f.get("tier") != "review":
                continue
            bpy_rank = rank_to_bpy.get(f.get("rank"))
            if bpy_rank is None:
                continue
            slot = "A" if f.get("defect_class") == "floating" else "C"
            tiers[int(bpy_rank)] = "A" if (tiers.get(int(bpy_rank)) == "A"
                                           or slot == "A") else "C"
        if tiers:
            return tiers

    verdict = load_jsonl_key(DATA_DIR / "part_verdicts.jsonl", key) or {}
    tier_by_rank = {d.get("rank"): d.get("tier") for d in verdict.get("decisions", [])
                    if d.get("tier") in ("A", "C")}
    tiers = {}
    for row in model.get("parts", []):
        tier = tier_by_rank.get(row.get("rank"))
        bpy_rank = row.get("engine_bpy_rank")
        if tier and bpy_rank is not None:
            tiers[int(bpy_rank)] = tier
    return tiers


def load_jsonl_key(path: Path, key: str) -> dict | None:
    """取该 key 的记录（无则 None）；解析走 common.read_jsonl，坏行会带 `文件名:行号`。"""
    return next((rec for rec in read_jsonl(path) if rec.get("key") == key), None)


def downscale_to(path: Path, res: tuple[int, int]) -> None:
    from PIL import Image

    img = Image.open(path)
    if img.size != tuple(res):
        img.resize(tuple(res), Image.LANCZOS).save(path)


def _legend_font(size: int):
    from PIL import ImageFont

    for name in ("msyh.ttc", "simhei.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:            # 旧版 Pillow 的 load_default 不收 size
        return ImageFont.load_default()


def post_process(out_dir: Path, checks_path: Path, res: tuple[int, int],
                 cfg: dict, compare_left: Path | None = None) -> list[str]:
    """渲染后处理：线框 SSAA 降回 →（可选）左右对比图。

    compare_left 给定时产出 compare_front.png（左 = compare_left 的 wire_front，
    右 = 本目录 wire_front，即"修复前 | 修复后"线框对比）；未给定不产出对比图。
    单步失败只告警不阻塞。返回新生成的文件名列表。

    缺陷局部放大图（`highlight_*_zoom.png`）已随逐检出定位图撤销，不再产出。
    """
    made: list[str] = []
    checks: dict = {}
    if checks_path.is_file():
        try:
            checks = json.loads(checks_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            checks = {}
    ssaa = int((checks.get("wire_display") or {}).get("ssaa", 1))
    if ssaa > 1:
        for v in GRID:
            f = out_dir / f"wire_{v}.png"
            if f.is_file():
                downscale_to(f, res)
                made.append(f"{f.name}(ssaa{ssaa}→{res[0]}x{res[1]})")
    if compare_left is not None:
        try:
            bg = hex_rgb(cfg["render"]["lookdev"]["bg_solid_color"])
            stitch_compare(compare_left, out_dir / "wire_front.png",
                           out_dir / "compare_front.png", bg)
            made.append("compare_front.png")
        except Exception as e:
            print(f"[warn] 对比图拼接失败：{e}")
    return made


def clean_legacy(data_dir: Path = DATA_DIR) -> list[str]:
    """白名单清理：删除 render/ 下不属于现役产物集的历史 png（旧命名单视图等）。

    判据是 `is_current_product`（单一来源）——只删"名族不在现役清单里"的图，
    不碰 json 产物（`blender_checks.json` / `locator.json` / `locator_marks.json`）
    与 `pieces/` 目录。
    """
    removed: list[str] = []
    for key in find_models(data_dir):
        rd = data_dir / "raw" / key / "render"
        if not rd.is_dir():
            continue
        for p in sorted(rd.iterdir()):
            if p.suffix.lower() == ".png" and not is_current_product(p.name):
                p.unlink()
                removed.append(f"{key}/{p.name}")
    return removed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Blender headless 渲染 + 原生检查")
    ap.add_argument("--keys", nargs="*", help="指定 data/raw 下的 key（默认全部）")
    ap.add_argument("--force", action="store_true", help="忽略已有产物强制重跑")
    ap.add_argument("--clean-old", action="store_true",
                    help="清理旧版单视图残留 png 后退出")
    ap.add_argument("--pieces-only", action="store_true",
                    help="只重跑单件隔离渲染（piece_<rank>_*.png，图证来源），"
                         "主通道不动——图证类改动用这个，几分钟级而非全量重渲")
    ap.add_argument("--target", choices=RENDER_TARGETS, default=None,
                    help="渲染目标（图种级独立重渲）：overview=整体图 / "
                         "highlight=判别着色 / wire=线框 / parts=配色诊断 / "
                         "pieces=单件图证 / all=全部；优先于旧旗标")
    args = ap.parse_args(argv)

    cfg = load_config()
    exe = get_blender_exe(cfg)
    res = tuple(cfg["render"]["resolution"])
    diag_ratio = float(cfg["detect"]["diag_ratio"])
    timeout = float(cfg["render"].get("timeout_seconds", 600))
    lookdev = cfg["render"].get("lookdev")
    if lookdev is None:
        raise ConfigError("config 缺少 [render.lookdev] 段（对照 config.example.toml）")

    # 组件配色诊断（PLAN-03 §6.1）：色板一次取满（取色与 rank 绑定，见 palette 模块说明），
    # render_one 按真实组件数截断，故编排器无需预知组件数。
    target = resolve_target(args)
    channels = target_channels(target)

    if args.clean_old:
        removed = clean_legacy()
        print(f"clean-old: 删除 {len(removed)} 个旧图" +
              (f"：{removed[:10]}{'…' if len(removed) > 10 else ''}" if removed else ""))
        return 0

    keys = args.keys or find_models()
    if not keys:
        print("没有待处理模型（data/raw/*/model.glb 不存在）")
        return 0

    failed: list[str] = []
    for key in keys:
        raw_dir = DATA_DIR / "raw" / key
        if not (raw_dir / "model.glb").is_file():
            print(f"[skip] {key} 无 model.glb")
            continue
        if target_complete(raw_dir, target) and not args.force                 and target != "pieces":
            print(f"[skip] {key} {target} 产物已齐")
            continue
        parts_cfg = {"channels": channels, "piece_files": []}
        if target == "locator":
            # 清理旧命名的定位图（曾叫 locator_<rank>_loc.png；现为 _model/_close 两机位）
            stale = [f for f in (raw_dir / "render").glob("locator_*.png")
                     if f.stem.rsplit("_", 1)[-1] not in ("model", "close")]
            for f in stale:
                f.unlink()
            if stale:
                print(f"[clean] {key} 删除旧命名定位图 {len(stale)} 张")
        if target == "pieces":
            gone = clean_stale_piece_views(raw_dir / "render")
            if gone:
                print(f"[clean] {key} 删除旧命名单件残留 {len(gone)} 张：{gone[:6]}")
        if target in ("pieces", "all"):
            parts_cfg["piece_files"] = [str(p) for p in
                                        export_review_pieces(key, raw_dir / "render")]
        if target == "locator":
            items = build_locator_items(key, raw_dir)
            # **只传路径**：条目含路径与方向，p11 有 94 条，塞进 argv 会撞 Windows
            # 命令行长度上限（实测 WinError 206「文件名或扩展名太长」，整批在第 11 个
            # 模型处中断）。Blender 侧读文件即可。
            parts_cfg["locator_items_path"] = str(raw_dir / "render" / "locator.json")
            parts_cfg["locator_ghost"] = str(raw_dir / "model.glb")
            parts_cfg["locator_tol_ratio"] = float(
                (load_config().get("detect") or {}).get("coloc_overlap_tol", 0.001))
            (raw_dir / "render").mkdir(parents=True, exist_ok=True)
            (raw_dir / "render" / "locator.json").write_text(
                json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
            if not items:
                print(f"[skip] {key} 无待复核检出，不产定位图")
                continue
        if target in ("highlight", "all"):
            parts_cfg["tiers"] = {str(k): v
                                  for k, v in build_tiers(key).items()}
        cmd = build_cmd(exe, raw_dir / "model.glb", raw_dir / "render",
                        raw_dir / "blender_checks.json", res, diag_ratio, lookdev,
                        parts_cfg)
        n_pieces_render = len((parts_cfg or {}).get("piece_files") or [])
        print(f"[render] {key} …（目标 {target}：{'/'.join(channels) if channels else '仅单件'}"
              f"{'，单件渲染 ' + str(n_pieces_render) + ' 件' if n_pieces_render else ''}）")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            failed.append(key)
            print(f"[fail] {key} 超时（>{timeout:g}s），已跳过")
            continue
        if proc.returncode != 0:
            failed.append(key)
            tail = "\n".join(proc.stderr.splitlines()[-15:])
            print(f"[fail] {key} exit={proc.returncode}\n{tail}")
            continue
        if target in ("pieces", "locator"):
            made = []
        else:
            made = post_process(raw_dir / "render", raw_dir / "blender_checks.json",
                                res, cfg)
        print(f"[ok] {key}（后处理：{', '.join(made) if made else '无'}）")

    if failed:
        print(f"render failed: {failed}")
        return 1
    print(f"render finished: {len(keys)} 个模型")
    return 0


if __name__ == "__main__":
    sys.exit(main())
