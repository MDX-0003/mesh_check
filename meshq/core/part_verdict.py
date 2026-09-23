"""§5 部件判别：三轴特征 → A/B/C 分档 + 理由串。

**纯逻辑**：无 IO、无 trimesh、阈值全部由调用方注入。输入是 part_features.py 产出的每个组件
特征行，输出是分档 + 由命中条件生成的理由串。特征定义与实测依据见 part_features.py 头部。

## 级联（优先级从高到低；条件一律是必要条件的合取，**不接受单轴触发**）

| 序 | 入口 | 条件 | 判定 |
|---|---|---|---|
| — | 主组件 | `is_main` | **B**（模型本体，永不自动删） |
| 0 | **A0 真实性门** | 双引擎都看见 **且** 非包围盒贴片 | 不满足 → **直接 C** |
| 1 | A1 形态极低 | `faces < min_faces` **且** `vol_ratio <= volume_eps` **且** 非封闭 | **A**（当前降级，见下） |
| 2 | A2 疏离且孤立 | `gap_ratio > iso_ratio` **且** 无同尺寸同类 | **A** |
| 3 | B 部件 | `gap_ratio <= iso_ratio` **且**（`diag_ratio >= diag_ratio_big` **或** `siblings >= group_min_siblings`） | **B** |
| 4 | C1 整组位移 | `gap_ratio > iso_ratio` **且** 有同尺寸同类 | **C** |
| 5 | C2 其余 | 贴合、尺寸小、且无同尺寸同类 | **C** |

## 四条设计取舍（都是实测逼出来的）

1. **A0 必须存在**：p01@smart-topology 的 3 个"2 面件"没有被 bpy(1e-4) 见证、且贴在模型包围盒
   平面上（bbox Y 恰好 ±0.5、面积 2.9e-06~3.5e-05）。**更强的证据是：它们到最近其它组件的
   距离是 0.0000**——即与其他几何共位，是"位置相同但法线/UV 不同"的重复几何（bpy 按位置合并、
   trimesh 按位置+法线+UV 量化去重，于是只有一侧保留）。没有这道门，A1 会把**文件格式的退化产物**
   当缺陷删掉（PLAN-03 §1.7）。实测：全批 11 件被此门拦下（p01@smart 3、p06 2、p07 2、p18 4）。
2. **B 不要求"封闭实体"**：原设计要求 `watertight + euler == 2`，实测把大量真部件推进了待审——
   p01@smart-topology 的三条腿（354/352/301 面，欧拉数 1/1/0）与 p06@standard 的两块 73 万面半壳
   （watertight=False）全是开放壳。低模与半壳本来就不封闭，用封闭性当"是部件"的门槛会系统性误伤。
   形态信息改为写进理由串（"（非封闭实体：…）"），不再作为门槛。
3. **"尺寸量级相当"不能只看对主组件的比例**：p07@standard 实测有 5 个各占对角线 0.17 倍、
   彼此等大的件——它们是一个多部件物体的组成部分，不是"碎屑"。故 B 的条件是**大 **或** 成群**
   （`siblings >= group_min_siblings`）。物理依据：生成伪影是随机散落的，**不会成组等大出现**；
   而成组等大恰恰是有意拆分（腿、水晶串、撑）的形态特征。
4. **A1 默认降级为 C**（`enable_a1=false`）：它唯一的样本已被 §1.7 推翻，语义仍成立但**没有实测
   支撑**。在 T2 全批筛出真候选并逐条目检确认之前，不用未取证的入口动资产（PLAN-03 §5.3）。

`is_solid` 保留为诊断量（进理由串、供标定参考），不再是 B 档的门槛。
理由串逐条可追溯（PLAN-03 §5.5），直接进报告与 flags。

改本模块必改 tests/test_part_verdict.py。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from meshq.core.common import DATA_DIR, load_config, read_jsonl_by_key
from meshq.core.findings import (CLASS_CO_LOCATED, CLASS_FLOATING, CLASS_NONE, CLASS_OTHER)

VERDICTS_FILE = DATA_DIR / "part_verdicts.jsonl"

TIER_FIX_ELIGIBLE = "A"   # 可自动删
TIER_KEEP = "B"           # 保留
TIER_REVIEW = "C"         # 待审，不自动处理

TIER_LABEL = {TIER_FIX_ELIGIBLE: "高置信伪影（可自动删）",
              TIER_KEEP: "高置信部件（保留）",
              TIER_REVIEW: "待审（不自动处理）"}

# 级联入口 → v2 缺陷类（PLAN-04 §三分诊映射；findings.py 是契约定义处）。
# A2/C1 是悬浮形态（C1 为群组位移）；A0 即共位；A1-off/C2 非核心类；本体/部件为 none。
DEFECT_CLASS_OF_ENTRY = {
    "main": CLASS_NONE, "B": CLASS_NONE,
    "A0": CLASS_CO_LOCATED,
    "A2": CLASS_FLOATING, "C1": CLASS_FLOATING, "A1": CLASS_FLOATING,
    "A1-off": CLASS_OTHER, "C2": CLASS_OTHER,
}


def _ex_num(value: float | None) -> str:
    """特征值 → 理由串里的短数字（None 显示为 —）。"""
    if value is None:
        return "—"
    if abs(value) >= 0.001:
        return f"{value:.3f}"
    return f"{value:.2e}"


def is_solid(part: dict[str, Any], cfg: dict[str, Any]) -> bool:
    """近似封闭实心件：水密 + 欧拉数 2（单连通闭合曲面）+ 面数达到实体下限。"""
    return (bool(part.get("watertight"))
            and int(part.get("euler") or 0) == 2
            and int(part.get("n_faces") or 0) >= int(cfg["solid_min_faces"]))


def has_same_size_peer(part: dict[str, Any]) -> bool:
    """是否有同尺寸同类（尺寸轴聚类后的兄弟数 >= 1）。"""
    return int(part.get("siblings") or 0) >= 1


def is_flimsy(part: dict[str, Any], cfg: dict[str, Any]) -> bool:
    """形态极低：面数低于下限，且 bbox 体积相对主组件近似为零，且非封闭。"""
    vol_ratio = part.get("vol_ratio")
    return (int(part.get("n_faces") or 0) < int(cfg["min_faces"])
            and vol_ratio is not None
            and float(vol_ratio) <= float(cfg["volume_eps"])
            and not bool(part.get("watertight")))


def a0_reject_reason(part: dict[str, Any], cfg: dict[str, Any]) -> str | None:
    """A0 真实性门：返回拦截理由；None = 通过。

    两条拦截（对应 PLAN-03 §1.7 的实测证据）：
    - 只在 trimesh 侧存在 → 该件的成立取决于焊接强度，不构成高置信缺陷；
    - 贴在模型包围盒平面上的退化片 → 文件格式产物，删掉视觉上无变化。
    """
    if bool(part.get("bbox_pinned")):
        return ("这是一块没有厚度的「贴片」：整体贴合模型包围盒边界、近乎零体积"
                "——是文件导出产生的退化产物，不像真实设计。为避免误删，转人工确认")
    if bool(cfg.get("require_bpy_agreement", True)) and not bool(part.get("engine_seen_by_bpy")):
        return ("这块组件只在其中一种解析精度下出现——换用更粗的焊接精度它就会并入本体，"
                "它的存在与否取决于解析参数，不是稳定的缺陷证据。为避免误删，转人工确认")
    return None


def decide(part: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """单个组件 → {tier, entry, reason}。多组件模型对每个非主组件各调一次。"""
    gap = part.get("gap_ratio")
    diag_ratio = part.get("diag_ratio")
    keep = f"faces={part.get('n_faces')}"

    if bool(part.get("is_main")):
        return {"tier": TIER_KEEP, "entry": "main",
                "reason": f"模型本体（最大的独立组件，{keep}），永不自动处理"}

    if bool(cfg.get("enable_a0", True)):
        blocked = a0_reject_reason(part, cfg)
        if blocked:
            return {"tier": TIER_REVIEW, "entry": "A0",
                    "reason": f"{blocked}（{keep}、尺寸比 {_ex_num(diag_ratio)}）"}

    if bool(cfg.get("enable_a1", False)) and is_flimsy(part, cfg):
        return {"tier": TIER_FIX_ELIGIBLE, "entry": "A1",
                "reason": (f"完全悬浮的碎片：面数极少（{keep}）、近乎零体积、非封闭"
                           f"——判定为生成伪影，自动删除")}
    if is_flimsy(part, cfg):
        return {"tier": TIER_REVIEW, "entry": "A1-off",
                "reason": (f"面数极少（{keep}）且近乎零体积，形态上像碎片；但这条规则还没有"
                           f"经过真实样本验证（此前曾误判过同类样本），保守不启用，转人工确认")}

    far = gap is not None and float(gap) > float(cfg["iso_ratio"])
    peer = has_same_size_peer(part)
    big = diag_ratio is not None and float(diag_ratio) >= float(cfg["diag_ratio_big"])
    grouped = int(part.get("siblings") or 0) >= int(cfg["group_min_siblings"])
    form = "" if is_solid(part, cfg) else "（注：该件是非封闭实体）"

    if far and not peer:
        return {"tier": TIER_FIX_ELIGIBLE, "entry": "A2",
                "reason": (f"完全悬浮：距模型其余部分 {_ex_num(gap)} 倍对角线，附近没有"
                           f"任何同尺寸组件——判定为生成伪影，自动删除（{keep}）")}
    if not far and (big or grouped):
        why = (f"尺寸达主体的 {_ex_num(diag_ratio)} 倍对角线" if big
               else f"有 {part.get('siblings')} 件同尺寸同类一起出现"
                    f"（生成伪影不会成组等大出现）")
        return {"tier": TIER_KEEP, "entry": "B",
                "reason": (f"与主体贴合（间隙 {_ex_num(gap)} 倍对角线），{why}"
                           f"——是设计部件，保留。{keep}{form}")}
    if far and peer:
        return {"tier": TIER_REVIEW, "entry": "C1",
                "reason": (f"整组悬浮：距模型其余部分 {_ex_num(gap)} 倍对角线，但同尺寸同类有 "
                           f"{part.get('siblings')} 件——可能是一整组被位移的设计部件，需人工判断去留（{keep}）")}
    return {"tier": TIER_REVIEW, "entry": "C2",
            "reason": (f"紧贴主体（间隙 {_ex_num(gap)} 倍对角线）、尺寸小（{_ex_num(diag_ratio)} 倍对角线）、"
                       f"也没有同尺寸同类——它与设计件在几何上无法区分，保守不自动处理，转人工确认（{keep}）{form}")}


def judge_model(model: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """一个模型的全部组件逐个判别 + 模型级汇总（供漏斗与报告）。"""
    decisions = []
    for part in model.get("parts", []):
        d = decide(part, cfg)
        decisions.append({**d, "rank": part.get("rank"),
                          "n_faces": part.get("n_faces"),
                          "diag_ratio": part.get("diag_ratio"),
                          "gap_ratio": part.get("gap_ratio"),
                          "defect_class": DEFECT_CLASS_OF_ENTRY.get(
                              d["entry"], CLASS_OTHER)})
    counts = {TIER_FIX_ELIGIBLE: 0, TIER_KEEP: 0, TIER_REVIEW: 0}
    for d in decisions:
        counts[d["tier"]] = counts.get(d["tier"], 0) + 1
    defect_counts = {CLASS_FLOATING: 0, CLASS_CO_LOCATED: 0,
                     CLASS_OTHER: 0, CLASS_NONE: 0}
    for d in decisions:
        defect_counts[d["defect_class"]] += 1
    return {
        "key": model.get("key"),
        "source": model.get("source"),
        "n_pieces": model.get("n_pieces"),
        "bpy_n_pieces": model.get("bpy_n_pieces"),
        "engine_partition_match": model.get("engine_partition_match"),
        "counts": counts,
        "defect_class_counts": defect_counts,
        "auto_fix_ranks": [d["rank"] for d in decisions if d["tier"] == TIER_FIX_ELIGIBLE],
        "review_ranks": [d["rank"] for d in decisions if d["tier"] == TIER_REVIEW],
        "decisions": decisions,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="组件分档（A 自动删 / B 保留 / C 待审）→ part_verdicts.jsonl")
    ap.add_argument("--parts-file", type=Path, default=DATA_DIR / "parts.jsonl")
    ap.add_argument("--out", type=Path, default=VERDICTS_FILE)
    args = ap.parse_args(argv)

    cfg = load_config()["parts"]
    models = read_jsonl_by_key(args.parts_file)
    if not models:
        print(f"无组件特征表：{args.parts_file}（先跑 part_features.py）")
        return 1

    out = []
    for key in sorted(models):
        rec = judge_model(models[key], cfg)
        out.append(rec)
        c = rec["counts"]
        print(f"[judge] {key:26s} A={c[TIER_FIX_ELIGIBLE]} B={c[TIER_KEEP]} "
              f"C={c[TIER_REVIEW]}  自动删={rec['auto_fix_ranks']} 待审={rec['review_ranks']}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n",
                        encoding="utf-8", newline="\n")
    print(f"part_verdicts.jsonl: {len(out)} 条 → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
