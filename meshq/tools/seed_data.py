"""seed_data · 数据装载与分发：管线要哪些数据、换一台机器怎么放回原位。

为什么有这个脚本：`data/` 整体不入库（体积 + 可重建性），于是"clone 之后跑不起来"
与"外部接收者不知道怎么恢复数据"是必然要回答的问题。把答案写成可执行动作，而不是
文档里的一段文字：**体检（缺什么）→ 打包（网盘三个包）→ 装载（解压到位 + 校验）**。

数据分组（group）与各自能支撑到哪一步：

    models    data/raw/<key>/model.glb + meta.json + thumbnail.png（生成产物）
              → 可重跑指标 / 特征 / 检出；装了 Blender 才有图
    renders   data/raw/<key>/render/* 与 pieces/*.glb（渲染中间件，~2GB）
              → 可**不装 Blender**重建交付页（图证来自这里）
    fixed     data/fixed/<key>/*（删减式修复的冻结件，报告"修复前后对比"要它）
    analysis  data/*.jsonl + data/*.json（指标/特征/检出/台账）+ prompts.jsonl
              → 可跳过计算直接重建报告与交付

用法：

    python -m meshq.tools.seed_data --check                     # 体检：各阶段输入齐不齐
    python -m meshq.tools.seed_data --list                      # 当前数据构成与体积
    python -m meshq.tools.seed_data --pack --out D:/dist        # 打包（默认 models+fixed+analysis）
    python -m meshq.tools.seed_data --pack --out D:/dist --groups models renders fixed analysis
    python -m meshq.tools.seed_data --unpack D:/dist            # 装载：解压到仓库根 + 逐文件校验

装载的安全性：包内每条都带 size + sha256（`_pack_manifest.json`），解压前先校验、
解压后再校验一遍，并对每个 GLB 跑 `common.glb_valid`——网盘/传输损坏会当场暴露，
而不是等到渲染时才发现模型是坏的（见 `.claude/memory/2026-09-21-test-polluted-real-data-glb-integrity.md`）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from meshq.core.common import (BATCH_SUMMARY_FILES, BATCH_SUFFIX, DATA_DIR, ROOT,
                                glb_valid, in_batch, load_config, read_jsonl)

PACK_PREFIX = "mesh-data"
MANIFEST_NAME = "_pack_manifest.json"

# 分组 → (说明, 相对仓库根的 glob)
GROUPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "models": ("原始模型（生成产物）", (
        "data/raw/*/model.glb",
        "data/raw/*/meta.json",
        "data/raw/*/thumbnail.png",
    )),
    "renders": ("渲染中间件（图证与交付页的图源，体积最大）", (
        "data/raw/*/blender_checks.json",
        "data/raw/*/render/*",
        "data/raw/*/pieces/*",
    )),
    "fixed": ("删减式修复冻结件", (
        "data/fixed/*/*.glb",
        "data/fixed/*/*.json",
        "data/fixed/*/render/*",
    )),
    "analysis": ("分析产物（指标/特征/检出/台账）", (
        "data/*.jsonl",
        "data/*.json",
        "prompts.jsonl",
    )),
}
DEFAULT_PACK_GROUPS = ("models", "fixed", "analysis")

# 阶段 → 需要的输入（用于体检报告）；None 表示"可选/缺失不阻塞"
STAGE_INPUTS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("generate", ("prompts.jsonl",), ()),
    ("render", ("data/raw/*/model.glb",), ("config.toml 的 blender 路径",)),
    ("inspect", ("data/raw/*/model.glb",), ()),
    ("detect", ("data/raw/*/model.glb",), ()),
    ("report", ("data/findings.jsonl", "data/raw/*/render"), ()),
    ("deliver", ("data/findings.jsonl", "data/raw/*/model.glb",
                 "data/raw/*/render"), ()),
    ("review-serve", ("publish",), ()),
)


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()




def _batch_of(rel: str) -> str | None:
    """仓库相对路径所属的批次（键形如 `data/raw/<pid>@<preset>/…`）；判定不出返回 None。"""
    parts = rel.split("/")
    if len(parts) >= 3 and parts[0] == "data" and parts[1] in ("raw", "fixed"):
        key = parts[2]
        for batch, suffix in BATCH_SUFFIX.items():
            if key.endswith(suffix):
                return batch
        return "probe"          # 无 @ 后缀的键（探针）不属于任何交付批次
    return None


def group_files(group: str, root: Path = ROOT, batch: str | None = None) -> list[Path]:
    """展开某分组的文件清单（去重、按相对路径排序）；`batch` 限定批次范围。

    只对**按目录分键**的分组（models / renders / fixed）做路径级过滤；`analysis` 的产物是
    混合记录的文件，走 `analysis_entries()` 逐记录裁剪。
    """
    patterns = GROUPS[group][1]
    seen: dict[str, Path] = {}
    for pattern in patterns:
        for p in sorted(root.glob(pattern)):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if batch and group != "analysis":
                got = _batch_of(rel)
                if got is not None and got != batch:
                    continue          # 别的批次，或不属于任何批次（探针）
            if batch and group == "analysis" and p.name in BATCH_ONLY_FILES                     and BATCH_ONLY_FILES[p.name] != batch:
                continue
            seen[rel] = p
    return [seen[k] for k in sorted(seen)]


def analysis_entries(root: Path, batch: str, stage: Path) -> list[tuple[str, Path]]:
    """把 analysis 产物按批次裁剪后写入暂存目录，返回 `[(仓库相对路径, 暂存文件)]`。

    为什么要裁剪而不是原样带：`metrics/parts/findings/…` 都是"每键一行"的混合文件——
    只打包 T2 批次时，标准批的记录留在里面会让接收方困惑（"这些键在 models 包里根本没有"）。
    逐记录按 key 后缀过滤，无 key 的记录（实验记录类）保留。
    """
    suffix = BATCH_SUFFIX[batch]
    out: list[tuple[str, Path]] = []
    for p in group_files("analysis", root, batch=None):        # 先取全量，再按文件类型裁剪
        rel = p.relative_to(root).as_posix()
        if p.name in BATCH_SUMMARY_FILES:
            if BATCH_SUMMARY_FILES[p.name] == batch:
                out.append((rel, p))
            continue
        if p.suffix == ".jsonl" and p.name != "prompts.jsonl":
            recs = [r for r in read_jsonl(p)
                    if r.get("key") is None or in_batch(r["key"], batch)]
            if not recs:
                continue
            dest = stage / p.name
            nl = chr(10)          # 换行用表达式拼，避免转义在工具链里被吃掉
            dest.write_text(nl.join(json.dumps(r, ensure_ascii=False) for r in recs) + nl,
                            encoding="utf-8", newline="\n")
            out.append((rel, dest))
            continue
        if p.name == "tasks.json":
            data = json.loads(p.read_text(encoding="utf-8"))
            recs = [r for r in data if in_batch(r.get("key", ""), batch)]
            dest = stage / p.name
            dest.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
            out.append((rel, dest))
            continue
        out.append((rel, p))                                   # prompts.jsonl 与无键小文件
    return out


def build_manifest(files: list[tuple[str, Path]]) -> dict:
    """逐文件 size + sha256；GLB 额外记 `glb=valid|invalid`（打包时就暴露坏文件）。

    入参是 `(仓库相对路径, 实际文件)`——分析产物被裁剪后会另存在暂存目录，两者不同。
    """
    entries = []
    for rel, p in files:
        entry = {"path": rel, "size": p.stat().st_size, "sha256": sha256(p)}
        if p.suffix.lower() == ".glb":
            entry["glb"] = "valid" if glb_valid(p) else "invalid"
        entries.append(entry)
    return {"version": 1, "files": entries}


def pack(groups: list[str], out_dir: Path, compress: bool = False,
         root: Path = ROOT, batch: str | None = None) -> list[Path]:
    """按分组打 zip（路径相对仓库根，解压即到位），返回产物路径。

    `batch` 限定批次（`smart-topology` / `standard`）：路径型分组按目录键过滤，
    分析产物逐记录裁剪后暂存再打包。默认不过滤（整库）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stage = None
    if batch:
        stage = Path(tempfile.mkdtemp(prefix="seed-stage-"))
    made: list[Path] = []
    try:
        made = _pack_groups(groups, out_dir, compress, root, batch, stage)
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
    return made


def _pack_groups(groups: list[str], out_dir: Path, compress: bool, root: Path,
                 batch: str | None, stage: Path | None) -> list[Path]:
    made: list[Path] = []
    for group in groups:
        files = (analysis_entries(root, batch, stage) if (batch and group == "analysis")
                 else [(p.relative_to(root).as_posix(), p)
                       for p in group_files(group, root, batch)])
        if not files:
            print(f"[seed] {group}: 无文件，跳过")
            continue
        manifest = build_manifest(files)
        bad = [e["path"] for e in manifest["files"] if e.get("glb") == "invalid"]
        zip_path = out_dir / f"{PACK_PREFIX}-{group}.zip"
        mode = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        with zipfile.ZipFile(zip_path, "w", mode) as z:
            z.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=1))
            for (rel, src) in files:
                z.write(src, rel)
        size = zip_path.stat().st_size
        print(f"[seed] {group}: {len(files)} 个文件 → {zip_path.name} "
              f"（{size / 1048576:.1f} MB，含 sha256 清单）")
        if bad:
            print(f"[seed]   注意：{len(bad)} 个 GLB 完整性校验未过：{bad[:3]}")
        made.append(zip_path)
    return made


def _load_manifest(source: Path) -> dict:
    if source.is_dir():
        raise ValueError("目录装载请用包文件（.zip）")
    with zipfile.ZipFile(source) as z:
        return json.loads(z.read(MANIFEST_NAME).decode("utf-8"))


def unpack(sources: list[Path], root: Path = ROOT, dry_run: bool = False) -> int:
    """校验 + 解压到仓库根 + 复检。返回 0 表示全部通过。"""
    total = restored = 0
    bad: list[str] = []
    for src in sources:
        manifest = _load_manifest(src)
        entries = manifest.get("files") or []
        total += len(entries)
        print(f"[seed] {src.name}: {len(entries)} 个文件")
        with zipfile.ZipFile(src) as z:
            for entry in entries:
                rel = entry["path"]
                if dry_run:
                    continue
                data = z.read(rel)
                if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    bad.append(rel)
                    continue
                dest = root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                restored += 1
                if rel.endswith(".glb") and not glb_valid(dest):
                    bad.append(f"{rel}（GLB 校验未过）")
    if dry_run:
        print(f"[seed] dry-run：共 {total} 个文件待装载，未写盘")
        return 0
    print(f"[seed] 装载 {restored}/{total} 个文件 → {root}")
    if bad:
        print(f"[seed] 校验失败 {len(bad)} 个：{bad[:5]}")
        return 1
    return 0


def check(root: Path = ROOT, cfg: dict | None = None) -> int:
    """体检：各阶段输入齐不齐 + 每条缺口的补救办法。返回缺失项数（0 = 全齐）。"""
    print(f"[seed] 仓库 {root}")
    print(f"[seed] 现有数据：")
    for group in GROUPS:
        files = group_files(group, root)
        size = sum(p.stat().st_size for p in files)
        print(f"         {group:9s} {len(files):4d} 个文件 {size / 1048576:9.1f} MB")
    print(f"[seed] 阶段输入：")
    missing_total = 0
    for stage, need, optional in STAGE_INPUTS:
        gaps = []
        for pattern in need:
            if not any(root.glob(pattern)):
                gaps.append(pattern)
        mark = "OK  " if not gaps else "缺  "
        note = f"（缺 {'、'.join(gaps)}）" if gaps else ""
        if optional and stage == "render":
            blender = (cfg or {}).get("blender", {}).get("path", "")
            note += "" if blender and Path(blender).is_file() else \
                f"（blender 可执行未就位：{blender or 'config 未填'}）"
        print(f"         {mark} {stage}{note}")
        missing_total += len(gaps)
    if missing_total:
        print("[seed] 补救：`python -m meshq.tools.seed_data --unpack <数据包目录>` 装载；"
              "或 `python -m meshq.pipeline generate --dry-run` 看重新生成要花多少 credits")
    return missing_total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="数据装载与分发（体检 / 打包 / 装载）")
    ap.add_argument("--check", action="store_true", help="体检各阶段输入是否齐备")
    ap.add_argument("--list", action="store_true", help="列出各组数据构成与体积")
    ap.add_argument("--pack", action="store_true", help="按分组打 zip（含 sha256 清单）")
    ap.add_argument("--unpack", nargs="*", type=Path, default=None,
                    help="装载：一个或多个 .zip（或含 zip 的目录）")
    ap.add_argument("--groups", nargs="*", choices=tuple(GROUPS),
                    default=list(DEFAULT_PACK_GROUPS), help="打包分组（默认三小包）")
    ap.add_argument("--out", type=Path, default=None, help="打包输出目录")
    ap.add_argument("--batch", choices=("smart-topology", "standard"), default=None,
                    help="打包/列表的批次范围（默认整库）：只带该批次的模型与图，"
                         "并把分析产物按记录裁剪到该批次")
    ap.add_argument("--compress", action="store_true",
                    help="打包时 deflate 压缩（默认 stored：这些资产本身已压缩，省 CPU）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写盘")
    args = ap.parse_args(argv)

    cfg = None
    try:
        cfg = load_config()
    except Exception as e:                       # 配置缺失不阻塞体检（新 clone 常见）
        print(f"[seed] 配置未就绪（{type(e).__name__}）：{e}")

    if args.unpack is not None:
        sources: list[Path] = []
        for item in args.unpack or [Path(".")]:
            if item.is_dir():
                sources += sorted(item.glob(f"{PACK_PREFIX}-*.zip"))
            else:
                sources.append(item)
        if not sources:
            print("[seed] 未找到数据包（*.zip）")
            return 1
        rc = unpack(sources, dry_run=args.dry_run)
        if rc == 0 and not args.dry_run:
            check(cfg=cfg)
        return rc

    if args.pack:
        out = args.out or (ROOT.parent / "dist")
        if args.dry_run:
            for group in args.groups:
                n = len(group_files(group, batch=args.batch))
                print(f"[seed] WOULD PACK {group}: {n} 个文件"
                      + (f"（批次 {args.batch}）" if args.batch else ""))
            print(f"[seed] → {out}")
            return 0
        made = pack(args.groups, out, batch=args.batch)
        if args.batch:
            print(f"[seed] 批次范围：{args.batch}（分析产物已按记录裁剪）")
        return 0 if made else 1

    if args.list:
        scope = f"（批次 {args.batch}）" if args.batch else ""
        for group, (label, patterns) in GROUPS.items():
            files = group_files(group, batch=args.batch)
            size = sum(p.stat().st_size for p in files)
            print(f"[seed] {group:9s} {label}：{len(files)} 个文件 "
                  f"{size / 1048576:.1f} MB{scope}")
        return 0

    return 1 if check(cfg=cfg) else 0


if __name__ == "__main__":
    sys.exit(main())
