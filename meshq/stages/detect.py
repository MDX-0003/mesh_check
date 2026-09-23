"""v2 检出编排器（PLAN-04 §三）：检测关节的薄编排，产出 findings.jsonl。

流程：parts.jsonl 特征行 → ModelContext → REGISTRY 检测器逐个 detect → 分诊残余
（级联的 A1-off/C2 非核心类，理由串沿用级联保证可追溯）→ findings.merge →
data/findings.jsonl；岛明细另落 data/islands.jsonl。--report 接 05 出报告。

分工与去重：
- 悬浮（floating）由岛层检出（连通结构视角，精确最小距离）——级联的 A2/C1
  是近邻视角，组内贴合的整组位移它看不见（实测 p11@smart-topology 整组 88 件
  距主体 4.85% 而级联近邻间隙≈0），v2 报告以岛层为准；级联结果保留在
  part_verdicts.jsonl 作交叉参照；
- 共位（co_located）由共位检出器（双证据，与级联 A0 同源）；
- 非核心类（other）取级联 A1-off/C2 判定；
- 同 (key, rank) 多检出口命中时按 findings.merge 的缺陷类优先级去重。

**只检出不删除**：本编排器不产出任何删除队列，不触碰 04 --fix
（PLAN-04 决策 1，自动删除留待抽样取证后另行开启）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from meshq.core.common import DATA_DIR, ROOT, load_config, read_jsonl_by_key
from meshq.core.detectors import (CoLocatedDetector, FloatingIslandDetector,
                       ModelContext, REGISTRY)
from meshq.core.findings import (CLASS_OTHER, TIER_REVIEW, count_by_class, make_finding,
                      merge, summarize, write_findings)
from meshq.core.part_verdict import decide

FINDINGS_FILE = DATA_DIR / "findings.jsonl"
ISLANDS_FILE = DATA_DIR / "islands.jsonl"
PARTS_FILE = DATA_DIR / "parts.jsonl"

BATCH_SUFFIX = {"smart-topology": "@smart-topology", "standard": "@standard"}


def _triage_other_rows(ctx: ModelContext, parts_cfg: dict) -> list[dict]:
    """分诊残余：级联判为非核心类（A1-off/C2）的组件 → other 检出行。"""
    rows = []
    for part in (ctx.feature or {}).get("parts", []):
        if part.get("is_main"):
            continue
        d = decide(part, parts_cfg)
        if d["entry"] not in ("A1-off", "C2"):
            continue
        rows.append(make_finding(
            key=ctx.key, detector="triage", defect_class=CLASS_OTHER,
            rank=int(part["rank"]), reason=d["reason"], tier=TIER_REVIEW,
            entry=d["entry"], n_faces=part.get("n_faces"),
            evidence={"cascade_tier": d["tier"]},
            metrics={"gap_ratio": part.get("gap_ratio"),
                     "diag_ratio": part.get("diag_ratio"),
                     "siblings": part.get("siblings")}))
    return rows


def run_one(key: str, feature: dict, cfg: dict,
            det_islands: FloatingIslandDetector,
            data_dir: Path | None = None) -> tuple[list[dict], dict | None]:
    """单个模型：跑全部检测关节 + 分诊残余 → (检出行, 岛明细)。"""
    parts_cfg = cfg["parts"]
    glb = (data_dir or DATA_DIR) / "raw" / key / "model.glb"
    ctx = ModelContext(key, glb, feature=feature,
                       weld_digits=int(parts_cfg["weld_digits"]))
    island_report = det_islands.island_report(ctx, cfg)
    rows = []
    for det in REGISTRY:
        # 岛层复用已算好的岛明细（detect() 内部会重算整段几何管线，逐对精确距离×2 不可接受）
        if det is det_islands:
            rows.append(det.rows_from_report(ctx, island_report))
        else:
            rows.append(det.detect(ctx, cfg))
    rows.append(_triage_other_rows(ctx, parts_cfg))
    return merge(rows), island_report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="v2 检出编排：检测关节 → findings.jsonl（只检出不删除）")
    ap.add_argument("--keys", nargs="*", help="只跑指定模型键")
    ap.add_argument("--batch", choices=("all", *BATCH_SUFFIX), default="all",
                    help="批次过滤：smart-topology / standard / all")
    ap.add_argument("--parts-file", type=Path, default=PARTS_FILE)
    ap.add_argument("--out", type=Path, default=FINDINGS_FILE)
    ap.add_argument("--islands-out", type=Path, default=ISLANDS_FILE)
    ap.add_argument("--report", action="store_true", help="检出完成后调用 05 生成报告")
    args = ap.parse_args(argv)

    cfg = load_config()
    features = read_jsonl_by_key(args.parts_file)
    if not features:
        print(f"无特征表：{args.parts_file}（先跑 part_features.py）")
        return 1
    keys = sorted(features)
    if args.keys:
        keys = [k for k in keys if k in set(args.keys)]
    elif args.batch in BATCH_SUFFIX:
        keys = [k for k in keys if k.endswith(BATCH_SUFFIX[args.batch])]

    det_islands = FloatingIslandDetector()
    all_rows: list[dict] = []
    island_lines: list[str] = []
    for key in keys:
        rows, island_report = run_one(key, features[key], cfg, det_islands)
        all_rows.extend(rows)
        if island_report is not None:
            island_lines.append(json.dumps(island_report, ensure_ascii=False))
        counts = count_by_class(rows)
        print(f"[detect] {key:26s} floating={counts['floating']} "
              f"co_located={counts['co_located']} other={counts['other']}")

    write_findings(args.out, all_rows)
    args.islands_out.parent.mkdir(parents=True, exist_ok=True)
    args.islands_out.write_text("\n".join(island_lines) + "\n", encoding="utf-8", newline="\n")
    summary = summarize(all_rows)
    print(f"检出汇总：悬浮 {summary['floating']['findings']} 条"
          f"（{summary['floating']['models']} 模型）· "
          f"共位 {summary['co_located']['findings']} 条"
          f"（{summary['co_located']['models']} 模型）· "
          f"非核心 {summary['other']['findings']} 条"
          f"（{summary['other']['models']} 模型）")
    print(f"findings: {len(all_rows)} 条 → {args.out}；岛明细 → {args.islands_out}")

    if args.report:
        import meshq.stages.report as report
        report_args = [] if args.batch == "all" else ["--batch", args.batch]
        return report.main(report_args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
