"""pipeline：全流程统一触发入口（PLAN-05 §五触发入口）。

薄壳编排：每个 stage 内部调用现有阶段模块的 `main()`，不复制任何业务逻辑；
各阶段仍可独立运行，此处只解决"流程要敲 5 条命令"的问题。

## stage 一览

    generate   generate（花 credits，批末自动备份）        手动触发，不入 all
    render     render（Blender 重渲，分钟级/模型）          手动触发，不入 all
    inspect    metrics → part_features → part_verdict       三段指标/特征/级联
    detect     inspect 三段 + detect → findings.jsonl
    report     report ×（smart-topology, standard）
    deliver    deliver → results/<日期>/                    （PLAN-05 §二）
    all        detect → report → deliver（只串免费段）

## dry-run 语义（每个 stage 必有）

--dry-run 打印**等效命令 + 影响面**，不执行任何写入、网络请求或 Blender 调用：
- generate：按 prompts 台账 × 档位算将新建的任务数与预计 credits
  （preview 单价 standard 20 / smart-topology 5，来自 Meshy 计费口径，非设备配置）；
- render：列出缺渲染产物的 key 清单；
- detect / report / deliver：列出将调用的子命令与将写出的产物。

改变行为的参数（--preset/--batch/--date 等）原样透传给对应阶段；本模块只编排。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from meshq.core.common import DATA_DIR, ROOT, read_jsonl

# Meshy preview 模式计费口径（credits/任务；与档位相关，非设备配置）
CREDITS_PER_TASK = {"standard": 20, "smart-topology": 5}

_BATCH_KEYS = {"smart-topology": "@smart-topology", "standard": "@standard"}


def _stage_module(dotted: str):
    """按文件名取阶段模块（`"meshq.stages.metrics"` → `metrics`）。

    阶段模块都是普通模块，可常规 import；延迟到非 dry-run 时才导入，是为了让
    `--dry-run` 不牵入 trimesh / PIL / jinja2 这些重量级依赖。
    """
    import importlib
    return importlib.import_module(dotted)


def _run(dotted: str, argv: list[str], dry: bool,
         plan_note: str | None = None) -> int:
    """dry-run 打印等效命令与影响面；否则进程内调用该阶段 main()。"""
    print(f"[pipeline] {'WOULD RUN' if dry else 'RUN'}: "
          f"python -m {dotted} {' '.join(argv)}".rstrip())
    if plan_note:
        print(f"[pipeline]   {plan_note}")
    if dry:
        return 0
    return _stage_module(dotted).main(argv)


# ---------------------------------------------------------------- 影响面计算

def _plan_generate(args) -> str:
    """generate 干跑影响面：按台账算将新建任务数与预计 credits。"""
    tasks_path = DATA_DIR / "tasks.json"
    existing: dict[str, dict] = {}
    if tasks_path.exists():
        for rec in json.loads(tasks_path.read_text(encoding="utf-8")):
            existing[rec["key"]] = rec
    prompts_path = ROOT / "prompts.jsonl"
    prompts = read_jsonl(prompts_path)
    preset = args.preset or "standard"
    if args.smoke:
        plan = [(prompts[0]["pid"], preset)] if prompts else []
    elif args.all:
        plan = [(p["pid"], preset) for p in prompts]
    else:
        plan = []
    todo = [pid for pid, _ in plan
            if existing.get(f"{pid}@{preset}", {}).get("status") != "SUCCEEDED"]
    credits = sum(CREDITS_PER_TASK.get(preset, 0) for _ in todo)
    return (f"{len(plan)} 个候选中 {len(todo)} 个待新建（档位 {preset}），"
            f"预计消耗 {credits} credits")


def _plan_render(args) -> str:
    """render 干跑影响面：列出缺渲染产物的 key。"""
    raw = DATA_DIR / "raw"
    keys = args.keys or sorted(d.name for d in raw.iterdir() if d.is_dir())
    todo = [k for k in keys
            if not (raw / k / "render" / "base_front.png").is_file()]
    if args.force:
        todo = keys
    return f"{len(todo)}/{len(keys)} 个模型缺渲染产物将渲染" + (
        f"：{', '.join(todo[:8])}{' …' if len(todo) > 8 else ''}" if todo else "")


def _plan_deliver(args) -> str:
    """deliver 干跑影响面：落位预览（复用 review.plan_dispositions）。"""
    from meshq.core.findings import read_findings
    from meshq.core.review import load_reviews, plan_dispositions
    rows = read_findings(DATA_DIR / "findings.jsonl")
    reviews = load_reviews(DATA_DIR / "review.jsonl")
    keys = sorted({d.name for d in (DATA_DIR / "raw").iterdir()
                   if (d / "model.glb").is_file()})
    if args.batch in _BATCH_KEYS:
        keys = [k for k in keys if k.endswith(_BATCH_KEYS[args.batch])]
    plan = plan_dispositions(rows, reviews, keys)
    counts: dict[str, int] = {}
    for p in plan.values():
        counts[p["disposition"]] = counts.get(p["disposition"], 0) + 1
    human = sum(1 for p in plan.values() if p["source"] == "human")
    return (f"{len(keys)} 个模型 → " +
            " / ".join(f"{k} {v}" for k, v in sorted(counts.items())) +
            f"（人工裁决 {human} 条优先）→ results/{args.date}/")


# ---------------------------------------------------------------- stages

def stage_inspect(argv: list[str], dry: bool) -> int:
    for dotted in ("meshq.stages.metrics", "meshq.core.part_features", "meshq.core.part_verdict"):
        rc = _run(dotted, argv, dry)
        if rc:
            return rc
    return 0


def stage_detect(argv: list[str], dry: bool) -> int:
    rc = stage_inspect(argv, dry)
    if rc:
        return rc
    return _run("meshq.stages.detect", argv, dry,
                plan_note="写出 data/findings.jsonl + data/islands.jsonl")


def stage_report(argv: list[str], dry: bool) -> int:
    for batch in ("smart-topology", "standard"):
        rc = _run("meshq.stages.report", ["--batch", batch] + argv, dry,
                  plan_note=f"写出 data/report_{'t2' if batch == 'smart-topology' else 'standard-v2'}.html")
        if rc:
            return rc
    return 0


def stage_deliver(argv: list[str], dry: bool) -> int:
    return _run("meshq.stages.deliver", argv, dry,
                plan_note=_plan_deliver(args_holder[0]) if dry else None)


def stage_generate(argv: list[str], dry: bool) -> int:
    return _run("meshq.stages.generate", argv, dry,
                plan_note=_plan_generate(args_holder[0]) if dry else None)


def stage_render(argv: list[str], dry: bool) -> int:
    return _run("meshq.stages.render", argv, dry,
                plan_note=_plan_render(args_holder[0]) if dry else None)


args_holder: list = []   # 当前 stage 的 argparse 结果，供干跑影响面计算


STAGES = {
    "generate": stage_generate,
    "render": stage_render,
    "inspect": stage_inspect,
    "detect": stage_detect,
    "report": stage_report,
    "deliver": stage_deliver,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="全流程统一入口（每阶段支持 --dry-run）")
    ap.add_argument("stage", choices=("generate", "render", "inspect", "detect",
                                      "report", "deliver", "all"))
    ap.add_argument("--dry-run", action="store_true",
                    help="打印等效命令与影响面，不执行任何写入/网络/Blender")
    ap.add_argument("--preset", choices=("standard", "smart-topology"),
                    help="generate：生成档位")
    ap.add_argument("--smoke", action="store_true", help="generate：1 条冒烟")
    ap.add_argument("--all", action="store_true", help="generate：20 条全量")
    ap.add_argument("--keys", nargs="*", help="render/detect：指定模型键")
    ap.add_argument("--batch", choices=("all", "smart-topology", "standard"),
                    default="all", help="detect/deliver：批次过滤")
    ap.add_argument("--date", default=None, help="deliver：results 目录日期段")
    ap.add_argument("--force", action="store_true", help="render：忽略已有产物")
    ap.add_argument("--pieces-only", action="store_true",
                    help="render：只重跑单件隔离渲染（图证类改动的快速通道，分钟级）")
    ap.add_argument("--target",
                    choices=("overview", "highlight", "wire", "pieces",
                             "locator", "all"),
                    help="render：渲染目标（图种级独立重渲，优先于旧旗标）")
    args = ap.parse_args(argv)
    if args.date is None:
        from datetime import date
        args.date = date.today().isoformat()

    global args_holder
    args_holder = [args]

    detect_args: list[str] = []
    if args.batch != "all":
        detect_args += ["--batch", args.batch]
    if args.keys:
        detect_args += ["--keys", *args.keys]
    deliver_args = detect_args + ["--date", args.date]

    if args.stage == "all":
        rc = stage_detect(detect_args, args.dry_run)
        if rc == 0:
            rc = stage_report([], args.dry_run)
        if rc == 0:
            rc = stage_deliver(deliver_args, args.dry_run)
        return rc
    if args.stage == "generate":
        g = []
        if args.smoke:
            g.append("--smoke")
        if args.all:
            g.append("--all")
        if args.preset:
            g += ["--preset", args.preset]
        return stage_generate(g, args.dry_run)
    if args.stage == "render":
        r = []
        if args.keys:
            r += ["--keys", *args.keys]
        if args.force:
            r.append("--force")
        if args.target:
            r += ["--target", args.target]
        return stage_render(r, args.dry_run)
    if args.stage == "detect":
        return stage_detect(detect_args, args.dry_run)
    if args.stage == "deliver":
        return stage_deliver(deliver_args, args.dry_run)
    return STAGES[args.stage]([], args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
