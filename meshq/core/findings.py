"""v2 检出结果契约：Finding 记录、合并与 findings.jsonl 读写（PLAN-04 §三）。

**为什么有这个模块**：v2 之前"检出"没有统一形态——报告同时读 flags.jsonl（legacy 三档）、
part_verdicts.jsonl（级联分档）与 metrics.jsonl，每加一类检出都要改报告内核。
findings.jsonl 是**报告的唯一检出输入**：一行 = 一个组件的一条检出，悬浮 / 共位两类
核心缺陷与非核心类全部从这里出报告；检测器（detectors.py）只对它负责。

## 字段（逐条可追溯，理由串人工可直接推翻）

| 字段 | 含义 |
|---|---|
| key | 模型键（pid@preset） |
| detector | 产出该检出的检测器（islands / co_located / triage） |
| defect_class | floating / co_located / other |
| tier | review / keep（v2 只产 review；**"auto" 为保留值，本模块拒绝写入**——两类检出只检出不删除，PLAN-04 决策 1，自动删除留待抽样取证后开启） |
| rank | 组件 rank（parts.jsonl 口径：weld → split_components 面数降序下标） |
| entry | 判据入口（沿用级联编号 A0/A2/C1/C2/A1-off；检测器自主检出用 island） |
| n_faces | 组件三角面数 |
| reason | 白话理由串（进报告与复核表） |
| evidence | 证据字段（检测器自定义 dict） |
| metrics | 量化特征（dict） |

defect_class 取 none 的组件（本体 / 设计部件）不落检出行，只参与模型级计数口径。

## 合并优先级

同一 (key, rank) 被多个检测器命中时保留**一个**行：先看 tier（review 的可执行
检出优先于 keep 的特性记录——共位界面贴合转 keep 后，整组位移的悬浮检出不应被
它吞掉），tier 相同再按缺陷类 co_located > floating > other（共位证据最硬：
存在性依赖解析 / 包围盒贴片；悬浮次之；非核心类让位）。

改本模块必改 tests/test_findings.py。
"""

from __future__ import annotations

import json
from pathlib import Path

from meshq.core.common import iter_jsonl

CLASS_FLOATING = "floating"
CLASS_CO_LOCATED = "co_located"
CLASS_OTHER = "other"
CLASS_NONE = "none"
DEFECT_CLASSES = (CLASS_FLOATING, CLASS_CO_LOCATED, CLASS_OTHER, CLASS_NONE)

TIER_REVIEW = "review"
TIER_KEEP = "keep"
TIER_AUTO_RESERVED = "auto"   # 保留值：取证后启用自动删时才允许，v2 拒绝写入
_FINDING_TIERS = (TIER_REVIEW, TIER_KEEP)

# 同 (key, rank) 冲突时的保留优先级（小者优先）：review 的可执行检出先于
# keep 的特性记录，tier 相同再按缺陷类；不在表中的类排在最后
_CLASS_PRIORITY = {CLASS_CO_LOCATED: 0, CLASS_FLOATING: 1, CLASS_OTHER: 2}


def _merge_key(row: dict) -> tuple:
    return (0 if row.get("tier") == TIER_REVIEW else 1,
            _CLASS_PRIORITY.get(row["defect_class"], 9))

_REQUIRED = ("key", "detector", "defect_class", "rank", "tier", "reason")


def make_finding(*, key: str | None = None, detector: str | None = None,
                 defect_class: str | None = None, rank: int | None = None,
                 reason: str | None = None, tier: str = TIER_REVIEW,
                 entry: str | None = None, n_faces: int | None = None,
                 evidence: dict | None = None,
                 metrics: dict | None = None) -> dict:
    """构造一条 Finding，字段与取值在这里做唯一校验。"""
    values = locals()
    for name in _REQUIRED:
        if values[name] is None:
            raise ValueError(f"finding 缺少必填字段：{name}")
    if defect_class not in DEFECT_CLASSES:
        raise ValueError(f"defect_class 非法：{defect_class}（可选：{DEFECT_CLASSES}）")
    if defect_class == CLASS_NONE:
        raise ValueError("none 类是保留口径（本体/设计部件），不落检出行")
    if tier == TIER_AUTO_RESERVED:
        raise ValueError(
            'tier="auto" 是保留值：v2 两类检出只检出不删除（PLAN-04 决策 1），'
            "自动删除留待抽样取证后另行开启")
    if tier not in _FINDING_TIERS:
        raise ValueError(f"tier 非法：{tier}（可选：{_FINDING_TIERS}，auto 为保留值）")
    rank = int(rank)
    if rank < 0:
        raise ValueError(f"rank 非法：{rank}")
    return {
        "key": str(key), "detector": str(detector), "defect_class": defect_class,
        "tier": tier, "rank": rank,
        "entry": (str(entry) if entry is not None else None),
        "n_faces": (int(n_faces) if n_faces is not None else None),
        "reason": str(reason),
        "evidence": dict(evidence or {}), "metrics": dict(metrics or {}),
    }


def merge(groups: list[list[dict]]) -> list[dict]:
    """多检测器的检出行合并：同 (key, rank) 按 tier+缺陷类优先级去重，按 (key, rank) 排序。"""
    best: dict[tuple[str, int], dict] = {}
    for rows in groups:
        for row in rows:
            k = (row["key"], int(row["rank"]))
            cur = best.get(k)
            if cur is None or _merge_key(row) < _merge_key(cur):
                best[k] = row
    return [best[k] for k in sorted(best, key=lambda x: (x[0], x[1]))]


def count_by_class(rows: list[dict]) -> dict[str, int]:
    """检出行的缺陷类计数（含零值项，供模型级汇总）。**按类，不按 tier**。"""
    return {c: sum(1 for r in rows if r["defect_class"] == c)
            for c in (CLASS_FLOATING, CLASS_CO_LOCATED, CLASS_OTHER)}


def count_for_display(rows: list[dict]) -> dict[str, int]:
    """页面展示口径的四类计数：三类"问题"只数待复核（review）档，贴合界面只数 keep 档。

    为什么必须按 tier 拆开（2026-09-23 实测的页面错误）：共位类里 `review` 是两类检出
    （真重叠 / 灰区，要人工看），`keep` 是界面贴合（拆件的正常形态，不是问题）。只按
    defect_class 计数会让同一批 keep 行**同时进"重叠面"盒子与"贴合界面"盒子**——
    p15 页面因此显示"重叠面 48 / 贴合界面 18"，而真正待复核的共位只有 30 条。
    本函数与裁决口径一致："检出"仅指 review 档（见 `.claude/memory/` 的裁决规则备忘）。

    返回 {floating, co_located, other, interface, review_total}。
    """
    review = [r for r in rows if r.get("tier") == TIER_REVIEW]
    counts = count_by_class(review)
    counts["interface"] = sum(
        1 for r in rows if r.get("tier") == TIER_KEEP
        and r["defect_class"] == CLASS_CO_LOCATED)
    counts["review_total"] = len(review)
    return counts


def summarize(rows: list[dict]) -> dict[str, dict[str, int]]:
    """全批检出汇总：每类 {findings: 检出条数, models: 覆盖模型数}（报告首屏用）。"""
    out = {}
    for c in (CLASS_FLOATING, CLASS_CO_LOCATED, CLASS_OTHER):
        sub = [r for r in rows if r["defect_class"] == c]
        out[c] = {"findings": len(sub),
                  "models": len({r["key"] for r in sub})}
    return out


def write_findings(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8", newline="\n")


def read_findings(path: Path) -> list[dict]:
    """findings.jsonl → 行列表；经 make_finding 重校验，损坏行直接报错不静默跳过。"""
    if not path.exists():
        return []
    rows = []
    for line_no, rec in enumerate(iter_jsonl(path), 1):
        try:
            rows.append(make_finding(**rec))
        except (TypeError, ValueError) as e:
            raise ValueError(f"{path.name}:{line_no} 检出行非法：{e}") from e
    return rows
