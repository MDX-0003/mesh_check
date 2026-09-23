"""渲染产物像素零差异回归（PLAN-03.1 §4 的安全网）。

用途：改动渲染/几何模块后，证明输出与既有产物**逐像素一致**。
做法：把 data/raw/<key>/render（或 data/fixed/<key>/render）下的既有产物当基线，
用当前代码渲到临时目录，逐文件比对尺寸与像素（三通道）。

**基线可信度警告**：既有产物未必都是当前代码路径产出的。仓库里出现过"某通道是修复前
遗留、而幂等跳过逻辑从未重渲它"的情况（2026-09-21，p13 的 wire_*.png），此时比对会报假差异。
判别办法：同一份代码连渲两次若彼此一致、而与基线不同，则基线陈旧；改用 `--golden` 指向
"改动前代码渲染的金样目录"再比对。详见 .claude/memory/_index.md。

这不是流水线阶段，与 backup.py 同类：只在重构与排查时手动跑，不进 CI。

用法：
  uv run python -m meshq.tools.render_regression                    # 默认样本集，三通道
  uv run python -m meshq.tools.render_regression --keys p13@standard
  uv run python -m meshq.tools.render_regression --golden <目录>      # 用金样目录替代既有产物作基线
退出码：0 = 全部一致；1 = 有差异或渲染失败。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]   # 仓库根（模块现位于 meshq/<层>/<模块>.py）
sys.path.insert(0, str(ROOT))
from meshq.core.common import DATA_DIR, get_blender_exe, load_config  # noqa: E402

# 覆盖：小面数（最快）/ 中面数 / 大面数 + 线壳降采样路径 / T2 低模
DEFAULT_KEYS = ("p01@smart-topology", "p01@standard", "p13@standard", "p18@standard")


def load_orchestrator():
    """渲染编排器（`render.py`）：去编号后可直接 import。"""
    import meshq.stages.render as render
    return render


def resolve_model(key: str) -> tuple[Path, Path] | None:
    """返回 (glb, 基线 render 目录)；raw 优先，其次 fixed。"""
    raw = DATA_DIR / "raw" / key
    if (raw / "model.glb").is_file():
        return raw / "model.glb", raw / "render"
    fixed = DATA_DIR / "fixed" / key
    if (fixed / "model_fixed.glb").is_file():
        return fixed / "model_fixed.glb", fixed / "render"
    return None


def compare_files(baseline: Path, produced: Path, names: list[str]) -> list[str]:
    """逐文件比对；返回不一致说明（空列表 = 完全一致）。"""
    bad: list[str] = []
    for name in names:
        b, p = baseline / name, produced / name
        if not p.is_file():
            bad.append(f"{name}: 未产出")
            continue
        if not b.is_file():
            bad.append(f"{name}: 基线缺失，跳过")
            continue
        if name.endswith(".json"):
            if b.read_bytes() != p.read_bytes():
                bad.append(f"{name}: JSON 内容不一致")
            continue
        ia, ib = Image.open(b).convert("RGB"), Image.open(p).convert("RGB")
        if ia.size != ib.size:
            bad.append(f"{name}: 尺寸 {ia.size} vs {ib.size}")
            continue
        a = np.asarray(ia, dtype=int)
        c = np.asarray(ib, dtype=int)
        d = int(np.abs(a - c).max())
        if d:
            bad.append(f"{name}: 最大像素差 {d}")
    return bad


def baseline_names(render_dir: Path) -> list[str]:
    """三通道产物名（第四通道"组件配色诊断"与 parts_legend 已随 PLAN-07 §五 撤销）。"""
    return sorted(p.name for p in render_dir.glob("*.png")
                  if not p.name.startswith("parts_"))


def run_one(rend, key: str, cfg: dict, workdir: Path,
            golden: Path | None = None) -> list[str]:
    found = resolve_model(key)
    if found is None:
        return [f"{key}: 无 GLB（raw/fixed 都没有）"]
    glb, baseline = found
    compare_against = golden or baseline
    if not compare_against.is_dir():
        return [f"{key}: 无对照产物 {compare_against}（无法比对）"]
    names = baseline_names(compare_against)
    if not names:
        return [f"{key}: 对照目录里没有可比对的文件"]

    out = workdir / key
    out.mkdir(parents=True, exist_ok=True)
    parts_cfg = {"channels": ["base", "highlight", "wire"]}
    cmd = rend.build_cmd(get_blender_exe(cfg), glb, out, out / "blender_checks.json",
                         tuple(cfg["render"]["resolution"]),
                         float(cfg["detect"]["diag_ratio"]),
                         cfg["render"]["lookdev"], parts_cfg)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=float(cfg["render"].get("timeout_seconds", 900)))
    except subprocess.TimeoutExpired:
        return [f"{key}: Blender 超时"]
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.splitlines()[-12:])
        return [f"{key}: Blender exit={proc.returncode}\n{tail}"]

    rend.post_process(out, out / "blender_checks.json",
                      tuple(cfg["render"]["resolution"]), cfg)
    bad = compare_files(compare_against, out, names)
    tag = "golden" if golden else "baseline"
    print(f"[{'ok' if not bad else 'diff'}] {key}（{tag}，{len(names)} 个文件）")
    for line in bad:
        print(f"      {line}")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="渲染产物像素零差异回归（PLAN-03.1）")
    ap.add_argument("--keys", nargs="*", default=list(DEFAULT_KEYS))
    ap.add_argument("--golden", type=Path,
                    help="用该目录（如改动前代码渲出的金样）替代既有产物作对照")
    ap.add_argument("--keep", action="store_true", help="保留临时目录（排查用）")
    args = ap.parse_args(argv)

    cfg = load_config()
    rend = load_orchestrator()
    workdir = Path(tempfile.mkdtemp(prefix="render_identity_"))
    print(f"样本 {len(args.keys)} 个 · 临时目录 {workdir}"
          f"{f' · 金样 {args.golden}' if args.golden else ''}\n")

    failures: list[str] = []
    for key in args.keys:
        failures += [f"{key}: {m}" for m in
                     run_one(rend, key, cfg, workdir, args.golden)]

    if not args.keep:
        shutil.rmtree(workdir, ignore_errors=True)
    if failures:
        print(f"\n不一致 {len(failures)} 处：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\n全部一致：最大像素差 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
