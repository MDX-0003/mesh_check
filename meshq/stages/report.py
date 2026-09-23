"""05 · 汇总报告：单文件 HTML（Jinja2 + base64 内嵌图）+ summary.json。

读 data/ 下的 tasks.json / metrics.jsonl / flags.jsonl → data/report.html + data/summary.json。
--batch smart-topology 产出 T2 批次报告（report_t2.html）。standard 批次报告冻结归档
（report-standard.html，15/5/1 不动，D9）。

**v2 检出口径（PLAN-04）**：批次存在 data/findings.jsonl 检出记录时启用 v2 版式——
- 首屏从"四档漏斗"改为**检出汇总**：悬浮碎片 / 共位双层面 / 非核心待审三盒，
  legacy 漏斗与阈值口径移入方法学节（details 折叠）；
- 卡片结论句为两类表述（"悬浮碎片 N 个、共位双层面 K 个；无其他检出"）；
- 复核表按**缺陷类分组**（悬浮组 / 共位组 / 非核心组），检出行来自 findings.jsonl
  （每条带证据与白话理由，人工可直接推翻）；
- **只检出不删除**：全文不出现自动删除表述，红/琥珀着色语义为"待人工复核"；
- legacy 碎片面率红线注记退役（岛层碎片化度量取代其职责）。
无检出记录的批次维持 legacy 版式（冻结归档口径可复现）。
卡片布局（每张图带一句"这张图回答什么"的图注；两组三视图）：
  修复前：判别结果着色 + 线框（叠加三视图 ×3）+ 待审组件逐个放大拼图
  修复后：网格线框三视图（未修复模型即原样入库形态）
  平滑着色三视图已撤下（不承担检查职能）；对比图仅修复过的模型显示
内嵌图统一降采样（max_width=420），控制单文件体积；逐组件放大拼图与复核表放宽到 1100px。
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from pathlib import Path

from jinja2 import Environment

from meshq.core.common import (DATA_DIR, load_config, read_jsonl,
                                read_jsonl_by_key)
from meshq.core.findings import (CLASS_CO_LOCATED, CLASS_FLOATING, CLASS_OTHER,
                      count_by_class, read_findings, summarize)
from meshq.core.part_verdict import TIER_FIX_ELIGIBLE, TIER_KEEP, TIER_REVIEW

REPORT_HTML = DATA_DIR / "report.html"
SUMMARY_JSON = DATA_DIR / "summary.json"
FINDINGS_FILE = DATA_DIR / "findings.jsonl"
EMBED_MAX_WIDTH = 420
GRID = ("front", "top", "right")
T2_PRESET = "smart-topology"

# 判别入口的白话标签（编号保留在括号里便于追溯到 PLAN-03 §5.2 真值表）——
# legacy 版式用；v2 复核表按缺陷类分组，不再按入口叙事排序
ENTRY_LABEL = {"A0": "真实性门（禁止自动删）", "A1-off": "碎片规则未启用（保守待审）",
               "C1": "疑似整组位移（需人工判断）", "C2": "几何不可分（保守不处理）"}

# v2 检出复核表的缺陷类标签与分组顺序（悬浮 → 共位 → 非核心）
CLASS_LABEL = {CLASS_FLOATING: "悬浮组（整组位移 / 独立岛）",
               CLASS_CO_LOCATED: "重叠面组（z-fighting 候选 + 灰区）",
               CLASS_OTHER: "非核心组（低优先人工）"}
CLASS_ORDER = (CLASS_FLOATING, CLASS_CO_LOCATED, CLASS_OTHER)

# 共位分型（与 detectors 的 coloc_true/coloc_partial 对应；仅用于展示分组）
COLoc_OVERLAP, COLoc_PARTIAL, COLoc_INTERFACE = ("overlap", "partial", "interface")

TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>Mesh-Test 几何质检报告</title>
<style>
  body { font-family: "Segoe UI", "Microsoft YaHei", sans-serif; margin: 24px;
         background: #f5f6f8; color: #222; }
  h1 { font-size: 22px; } h2 { font-size: 17px; margin-top: 28px; }
  h3 { font-size: 14px; margin: 14px 0 6px; color: #444; }
  .funnel { display: flex; gap: 12px; margin: 14px 0; }
  .funnel .box { padding: 10px 18px; border-radius: 8px; color: #fff; font-weight: 600; }
  .ok   { background: #2e8b57; } .fix { background: #d18b1f; } .rej { background: #b03a3a; }
  .rvw  { background: #5b6ac5; } .feat { background: #66707d; }
  .meta { color: #666; font-size: 13px; }
  .card { background: #fff; border-radius: 10px; padding: 14px 16px; margin: 14px 0;
          box-shadow: 0 1px 4px rgba(0,0,0,.08); }
  .row { display: flex; flex-wrap: wrap; gap: 6px; }
  .row img { width: 260px; border-radius: 6px; background: #ddd; }
  .thumb img { height: 200px; border-radius: 6px; background: #ddd; margin-right: 10px; }
  .badges span { display: inline-block; background: #eef1f5; border-radius: 4px;
                 padding: 2px 8px; margin: 2px 4px 2px 0; font-size: 12px; }
  .prompt { color: #555; font-style: italic; margin: 6px 0; }
  .verdict-direct { color: #2e8b57; font-weight: 700; }
  .verdict-fix { color: #d18b1f; font-weight: 700; }
  .verdict-reject { color: #b03a3a; }
  .verdict-review { color: #5b6ac5; font-weight: 700; }
  table { border-collapse: collapse; font-size: 13px; }
  td, th { border: 1px solid #dde; padding: 4px 10px; text-align: left; }
  .caption { font-size: 12px; color: #888; }
</style>
</head>
<body>
<h1>生成式 3D 资产几何质检报告</h1>
<p class="meta">生成 {{ funnel.total }} 个模型 · 检测目标：不应该存在的组件（悬浮碎片 / 共位双层面）·
判定阈值：对角线比 &lt; {{ thresholds.diag_ratio }}，碎片面率 &gt; {{ thresholds.frag_reject_ratio }} 拒绝 ·
引擎：{{ engines.blender }} + trimesh 交叉验证 ·
渲染：EEVEE lookdev 场景（平滑 / 红色高亮 / 线壳线框三通道），统一归一化尺度（bbox 最长边=1）</p>

{% if detections %}
<h2>检出汇总</h2>
<div class="funnel">
  <div class="box rej">悬浮碎片 {{ detections.summary.floating.findings }}
      （{{ detections.summary.floating.models }} 模型）</div>
  <div class="box rvw">重叠面 {{ detections.summary.co_located.findings }}
       （{{ detections.summary.co_located.models }} 模型）</div>
  <div class="box fix">非核心待审 {{ detections.summary.other.findings }}
       （{{ detections.summary.other.models }} 模型）</div>
  <div class="feat">部件贴合界面 {{ detections.summary.interface.findings }}
       （{{ detections.summary.interface.models }} 模型）· 导出特性</div>
</div>
<p class="meta">两类核心形态——悬浮碎片（独立飘离主体的小块）与重叠面（两套表面叠在
同一位置，渲染时来回闪烁）——由 v2 检测管线检出；<b>全部只检出、不自动处理，逐条
待人工复核</b>。部件贴合界面（多组件拆分时对接界面的顶点重合）是 T2 拆件的正常形态，
<b>非缺陷</b>，转为此处的导出特性度量，不占复核队列。每条检出带证据与白话理由
（见按缺陷类分组的复核表），人工可直接推翻。legacy 入库漏斗与阈值口径移入方法学节。</p>
{% if detections.noncore_note %}<p class="meta">{{ detections.noncore_note }}</p>{% endif %}
{% else %}
<h2>入库漏斗</h2>
<div class="funnel">
  <div class="box ok">直接入库 {{ funnel.direct }}</div>
  <div class="box fix">修复后入库 {{ funnel.fix }}</div>
  {% if t2 %}<div class="box rvw">待审 {{ funnel.review }}</div>{% endif %}
  <div class="box rej">拒绝 {{ funnel.reject }}</div>
</div>
{% if t2 %}<p class="meta">本批漏斗由部件判别级联驱动：修复后入库 = 含 A 档自动删组件的模型；
待审 = 含 C 档组件的模型（交人工复核，不自动处理）；直接入库 = 全部组件判定为部件/本体。
legacy 碎片面率红线（standard 口径）不再作为本批次档位，检出时在卡片注记。
本批 20 个模型全部含待审组件（共位重复类）——这是 T2 生成的普遍质量特征，而非判定错误；
A 档在零取证下保持关闭，自动删除数为 0 是诚实结论。</p>{% endif %}
{% endif %}

{% if detections %}
<details>
<summary style="cursor:pointer">方法学节：legacy 漏斗与阈值口径（点开/收起）</summary>
<p class="meta">legacy 入库漏斗：直接入库 {{ funnel.direct }} / 修复后入库 {{ funnel.fix }} /
{% if t2 %}待审 {{ funnel.review }} / {% endif %}拒绝 {{ funnel.reject }}。
legacy 碎片面率红线已退役（standard 口径在 T2 多组件形态下语义失真），碎片化改由岛层的
壳数/岛数度量承担；非流形边、开放边界与退化几何按留档口径逐模型给出一行说明。</p>
</details>
{% endif %}

<h2>模型卡片</h2>
{% for c in cards %}
<div class="card">
  <div class="thumb"><img src="data:image/png;base64,{{ c.thumb }}" alt="iso"></div>
  <div><b>{{ c.key }}</b>（{{ c.category }}） —
       <span class="verdict-{{ c.verdict_cls }}">{{ c.verdict }}</span></div>
  <div class="prompt">"{{ c.prompt }}"</div>
  {% if c.conclusion %}<p class="caption" style="font-size:13px;color:#333;font-weight:600">{{ c.conclusion }}</p>{% endif %}
  <div class="badges">
    <span>faces {{ c.n_faces }}</span><span>独立组件 {{ c.n_pieces }}</span>
    {% if c.detect_counts %}<span>悬浮 {{ c.detect_counts.floating }}</span>
    <span>重叠共位 {{ c.detect_counts.co_located }}</span>
    <span>贴合界面 {{ c.detect_counts.interface }}</span>
    <span>非核心 {{ c.detect_counts.other }}</span>{% endif %}
    {% if c.part_counts %}<span>待审件 {{ c.part_counts.C }}</span>
    <span>伪影件 {{ c.part_counts.A }}</span>{% endif %}
    <span>credits {{ c.credits }}</span>
  </div>
  {% if c.nm_note %}<p class="caption">{{ c.nm_note }}</p>{% endif %}
  {% if c.legacy_note %}<p class="caption">{{ c.legacy_note }}</p>{% endif %}

  {% if c.fixed %}
  <h3>修复前 · 判别结果着色 + 线框（叠加三视图：正 / 顶 / 侧）</h3>
  {% if detections %}
  <p class="caption">红 = 悬浮碎片检出（待人工复核）· <b>琥珀 = 重叠面与待人工组件</b> ·
  灰 = 判定为设计部件/本体，保留；深色线 = 真实几何边线。重叠面在渲染画面里通常没有
  独立可见轮廓（贴合界面类属正常形态、不进复核表）——完整清单以检出汇总与按类分组的
  复核表为准，数量以复核表为准。</p>
  {% else %}
  <p class="caption">红 = A 档（自动删除候选）· <b>琥珀 = C 档：需要人工复审</b>（对照下方复核表逐条判断）·
  灰 = 判定为设计部件/本体，保留；深色线 = 真实几何边线。大部分模型看不到彩色是正常的：它们的待审组件
  属于"共位重复"类，在渲染画面里本来就没有独立几何——完整清单见复核表，数量以复核表为准。</p>
  {% endif %}
  <div class="row">{% for u in c.before_hl %}<img src="data:image/png;base64,{{ u }}">{% endfor %}</div>
  <h3>修复后 · 网格线框三视图</h3>
  <p class="caption">处理完成后模型的结构视图（高面数模型为显示密度副本）：查结构与边走向，
  与上方叠加视图对照即可看出删除了什么。</p>
  <div class="row">{% for u in c.after_wire %}<img src="data:image/png;base64,{{ u }}">{% endfor %}</div>
  {% if c.compare %}
  <h3>修复前后 · 线框对比（左：修复前 | 右：修复后）</h3>
  <p class="caption">两侧均为线框渲染，几何差异直接可见。</p>
  <div class="row"><img src="data:image/png;base64,{{ c.compare }}" style="width:100%;max-width:1100px"></div>
  {% endif %}
  <div class="meta">修复：faces {{ c.fix_diff.before_faces }}→{{ c.fix_diff.after_faces }}，
  组件 {{ c.fix_diff.before_pieces }}→{{ c.fix_diff.after_pieces }}（删除 {{ c.fix_diff.removed_faces }} 面）</div>
  {% else %}
  <h3>判别结果着色 + 线框（叠加三视图：正 / 顶 / 侧）</h3>
  {% if detections %}
  <p class="caption">红 = 悬浮碎片检出（待人工复核）· <b>琥珀 = 重叠面与待人工组件</b> ·
  灰 = 判定为设计部件/本体，保留；深色线 = 真实几何边线。重叠面在渲染画面里通常没有
  独立可见轮廓（贴合界面类属正常形态、不进复核表）——完整清单以检出汇总与按类分组的
  复核表为准，数量以复核表为准。</p>
  {% else %}
  <p class="caption">红 = A 档（自动删除候选）· <b>琥珀 = C 档：需要人工复审</b>（对照下方复核表逐条判断）·
  灰 = 判定为设计部件/本体，保留；深色线 = 真实几何边线。大部分模型看不到彩色是正常的：它们的待审组件
  属于"共位重复"类，在渲染画面里本来就没有独立几何——完整清单见复核表，数量以复核表为准。</p>
  {% endif %}
  <div class="row">{% for u in c.before_hl %}<img src="data:image/png;base64,{{ u }}">{% endfor %}</div>
  <h3>网格线框三视图</h3>
  <p class="caption">模型的结构视图（未修复模型即原样入库形态；高面数模型为显示密度副本）：查结构与边走向。</p>
  <div class="row">{% for u in c.after_wire %}<img src="data:image/png;base64,{{ u }}">{% endfor %}</div>
  {% endif %}
</div>
{% endfor %}

{% if detections %}
<h2>检出复核表（按缺陷类分组，逐条人工裁决：删 / 留 / 改判）</h2>
{% for cls in detections.class_order %}
{% set g = detections.groups[cls] %}
<h3>{{ g.label }} · {{ g.total }} 条</h3>
{% if g.total == 0 %}<p class="meta">本批次无此类检出。</p>{% endif %}
{% for m in g.models %}
<div class="card">
  <div><b>{{ m.key }}</b> — {{ g.label }} {{ m.rows|length }} 件（下表逐条复核）</div>
  <table>
    <tr><th>#rank</th><th>faces</th><th>尺寸比</th><th>间隙比</th><th>证据</th><th>判定理由</th></tr>
    {% for r in m.rows %}
    <tr><td>{{ r.rank }}</td><td>{{ r.n_faces }}</td><td>{{ r.diag_ratio }}</td>
        <td>{{ r.gap_ratio }}</td><td>{{ r.evidence_str }}</td><td>{{ r.reason }}</td></tr>
    {% endfor %}
  </table>
</div>
{% endfor %}
{% endfor %}
<p class="meta">裁决约定：每条检出给出删 / 留 / 改判三选一结论；理由串由命中条件自动生成，
可直接推翻。共位类建议抽样 20~30 条确认删除安全性后再议处置；悬浮类逐条目检。</p>
{% else %}
{% if t2 %}
<h2>部件判别分区</h2>
<p class="meta">判别数据：part_verdicts.jsonl（三轴判据 + 级联，每条判定带理由串，可争议、可复核）。</p>

<h3>A 档 · 高置信伪影（自动删除清单）</h3>
{% if parts.a_rows %}
<table>
  <tr><th>key</th><th>组件 rank</th><th>faces</th><th>判定理由</th></tr>
  {% for r in parts.a_rows %}
  <tr><td>{{ r.key }}</td><td>{{ r.rank }}</td><td>{{ r.n_faces }}</td><td>{{ r.reason }}</td></tr>
  {% endfor %}
</table>
{% else %}
<p class="meta">零样本：本批次未出现 A 档判定。真伪影在现有语料中均为缝隙内贴合型（几何上与
设计件不可分），按设计进入 C 档待审——自动删除路径保持关闭，直到有逐条目检确认过的真候选。
这是判别器的诚实结论，不为凑"自动修复"而放宽门槛。</p>
{% endif %}

<h3>C 档 · 待审复核表（人工队列，目标每条 30 秒可判完）</h3>
<details {% if t2 %}open{% endif %}>
<summary style="cursor:pointer">先看这里：字段与规则对照（点开/收起）</summary>
<table>
  <tr><th>字段/名词</th><th>含义</th></tr>
  <tr><td>#rank</td><td>组件编号（按组件面数从多到少排序）——渲染图例与放大拼图上的 #N 就是它。
      "组件"指焊接后彼此分离的独立连通部分，一个模型可以由多个组件组成</td></tr>
  <tr><td>faces</td><td>该组件的三角面数</td></tr>
  <tr><td>尺寸比</td><td>组件包围盒对角线 ÷ 主组件对角线；越大组件越大（≥0.30 直接判"设计部件"保留）</td></tr>
  <tr><td>间隙比</td><td>组件到最近几何的表面距离 ÷ 主组件对角线；0 = 贴着，越大越悬浮（&gt;0.05 判"远离"）</td></tr>
  <tr><td>入口</td><td>命中的判别规则——<b>A0</b> 真实性门（双引擎划分不一致/包围盒贴片，<b>禁止自动删</b>，保守待审）；
      <b>A1-off</b> 形态极低但该入口尚未取证，保守待审；
      <b>C1</b> 整体远离但同尺寸成组，疑似整组位移；
      <b>C2</b> 贴合、尺寸小、无同类——几何上与设计件不可分，保守不处理</td></tr>
  <tr><td>判定理由</td><td>由命中条件自动生成的解释；人工复核可以直接推翻</td></tr>
</table>
</details>
{% for g in parts.c_groups %}
<div class="card">
  <div><b>{{ g.key }}</b> — 待审 {{ g.rows|length }} 件（下表逐条复核）</div>
  <table>
    <tr><th>#rank</th><th>faces</th><th>尺寸比</th><th>间隙比</th><th>入口</th><th>判定理由</th></tr>
    {% for r in g.rows %}
    <tr><td>{{ r.rank }}</td><td>{{ r.n_faces }}</td><td>{{ r.diag_ratio }}</td>
        <td>{{ r.gap_ratio }}</td><td>{{ r.entry_label }}（{{ r.entry }}）</td><td>{{ r.reason }}</td></tr>
    {% endfor %}
  </table>
</div>
{% endfor %}
{% if not parts.c_groups %}<p class="meta">本批次无待审件。</p>{% endif %}
<p class="meta">建议复核路径：A0 件抽样 20~30 条（它们是"自动删安全性"的关键证据）；
C2 件全量仅 {{ parts.c2_count }} 件，可全检；每条目标 30 秒内判完（结论：删 / 留 / 改判）。</p>
{% endif %}
{% endif %}

<h2>双引擎交叉验证</h2>
<table>
  <tr><th>key</th><th>组件 (bpy / trimesh)</th><th>非流形边 (bpy / trimesh)</th></tr>
  {% for x in crosscheck %}
  <tr><td>{{ x.key }}</td><td>{{ x.pieces_bpy }} / {{ x.pieces_trimesh }}</td>
      <td>{{ x.nm_bpy }} / {{ x.nm_trimesh }}</td></tr>
  {% endfor %}
</table>
<p class="meta">差异来源：两引擎焊接强度不同（bpy remove_doubles 1e-4 m vs trimesh merge 1e-5 m），
trimesh 侧会保留更多接触式分离组件。判定取两者保守值。</p>
</body>
</html>
"""

VERDICT_CLS = {"直接入库": "direct", "修复后入库": "fix", "拒绝": "reject",
               "待审": "review", "待复核": "review", "无检出": "direct"}


def load_part_verdicts(data_dir: Path) -> dict[str, dict]:
    """part_verdicts.jsonl → {key: 判别记录}；文件缺失返回空表（退回三档口径）。"""
    return read_jsonl_by_key(data_dir / "part_verdicts.jsonl")


def disposition_of(flag: dict, pv: dict | None) -> str:
    """模型级四档判定——T2 批次由判别器驱动（PLAN-03 §5.4，D6 修订 2026-09-21）：

    - A 档件 > 0 → 修复后入库（含自动删）；
    - C 档件 > 0 → 待审（不把不确定伪装成结论）；
    - 其余 → 直接入库。
    legacy 碎片面率红线是 standard 口径（假设主体占绝大部分面积），在 T2 多组件
    形态下被设计部件本身撑高、语义失真，不再作为 T2 档位——检出时以卡片注记呈现
    （build_card 的 legacy_note）。无判别表时退回旧三档，不编造结论。
    """
    if pv is None:
        return flag["verdict"]
    counts = pv.get("counts", {})
    if counts.get(TIER_FIX_ELIGIBLE, 0) > 0:
        return "修复后入库"
    if counts.get(TIER_REVIEW, 0) > 0:
        return "待审"
    return "直接入库"


# ---------------------------------------------------------------- v2 检出（PLAN-04）

def finding_reason_text(row: dict) -> str:
    """检出的白话展示文本：由 finding 里的数字与子类型即时生成（PLAN-05 A 项）。

    检出器只落简洁事实串与量化字段；措辞集中在这一处模板——文案迭代改这里即可，
    05/06 下一次渲染即生效，**无需重跑检出**（几何数字不变，文本是它们的
    确定性函数）。未识别的检出器回退 finding 自带的事实串。
    """
    m = row.get("metrics") or {}
    ev = row.get("evidence") or {}
    if row["defect_class"] == CLASS_CO_LOCATED:
        frac = m.get("overlap_frac")
        neighbor = ev.get("neighbor_rank")
        pct = f"{frac:.0%}" if frac is not None else "?"
        subtype = ev.get("co_located_subtype")
        if subtype == COLoc_OVERLAP:
            return (f"存在三角面重叠：该组件 {pct} 的表面积与组件 #{neighbor} 重合，"
                    "存在 z-fighting 风险（渲染时两套面来回闪烁）。"
                    "只检出不自动处理，待人工复核")
        if subtype == COLoc_PARTIAL:
            return (f"疑似三角面重叠（灰区）：该组件 {pct} 的表面积与组件 #{neighbor} "
                    "重叠——介于正常贴合与真重叠之间，优先目检确认。"
                    "只检出不自动处理，待人工复核")
        t_partial = m.get("coloc_partial_threshold")
        th = f"{t_partial:.0%}" if t_partial is not None else "阈值"
        return (f"部件贴合界面（非缺陷）：与组件 #{neighbor} 仅沿分界面对接，无面积"
                f"重叠（{pct}，低于阈值 {th}）——T2 拆件时对接界面的顶点完全一致，"
                "属正常形态。计为导出特性，不进复核队列")
    if row["defect_class"] == CLASS_FLOATING:
        shards = (m.get("island_shards") or 1)
        gap = m.get("gap_ratio")
        eps = m.get("eps_ratio")
        if shards > 1:
            return (f"整组悬浮：该组件与另外 {shards - 1} 个组件构成与主体分离的"
                    f"独立视觉岛（最近距离 {gap} 倍对角线，分离阈值 {eps}）——"
                    "可能是一整组被位移的设计部件。只检出不自动处理，待人工复核")
        return (f"悬浮碎片：所在视觉岛与主体完全分离（最近距离 {gap} 倍对角线，"
                f"分离阈值 {eps}，岛内无其他组件）。只检出不自动处理，待人工复核")
    return row.get("reason", "")


def _pct(ratio: float | None) -> str:
    """比例 → 百分比文本（读者不需要 5 位小数）。"""
    return f"{ratio:.1%}" if ratio is not None else "?"


def evidence_str(row: dict) -> str:
    """检出行 → 复核表里的**判据与量化**旁注。

    与 `finding_problem_text` 的分工（2026-09-23 改定）：问题列说"出了什么事、要不要人看"
    （白话结论），本函数说"**凭什么这么判**"（阈值、比例、岛规模、退化标记）。
    两者不重复同一条信息——此前灰区行的旁注把问题列的白话又抄了一遍，读者看不到判据。

    为什么不再输出"另一划分口径下未找到对应件"（2026-09-23）：那是跨引擎对账字段
    `engine_seen_by_bpy` 的读法，在 T2 的 102 条待复核共位行里出现 **90 条（88%）**，
    逐行重复等于噪声；两套引擎的口径差异改为在模型页页头说明一次（见 `deliver._engine_note`）。
    """
    ev = row.get("evidence", {})
    metrics = row.get("metrics") or {}
    if row["defect_class"] == CLASS_CO_LOCATED:
        frac = metrics.get("overlap_frac")
        subtype = ev.get("co_located_subtype")
        judged = {COLoc_OVERLAP: f"判据：面积重叠率 {_pct(frac)} ≥ 60%（两套面真重合）",
                  COLoc_PARTIAL: f"判据：面积重叠率 {_pct(frac)} 处于[20%, 60%]，需要人工确认正确性",
                  COLoc_INTERFACE: f"判据：面积重叠率 {_pct(frac)} < 20%（贴合界面，非缺陷）",
                  }.get(subtype, "判据：共位双证据（焊接后连通性消失 + 表面积重叠率）")
        if ev.get("bbox_pinned"):
            judged += "；该件是无厚度薄片、贴在模型外框平面上（疑似导出产生的退化面）"
        return judged
    if row["defect_class"] == CLASS_FLOATING:
        shards = metrics.get("island_shards") or 1
        share = metrics.get("island_face_share")
        eps = metrics.get("eps_ratio")
        judged = (f"岛层判据：与主体最近距离 > 整体 AABB 对角线的 {_pct(eps)}"
                  if eps is not None else "岛层判据：与主体无接触")
        judged += f"；该岛共 {shards} 个组件"
        if share is not None:
            judged += f"，合计占模型面数的 {_pct(share)}"
        return judged
    if row["defect_class"] == CLASS_OTHER:
        bits = []
        if metrics.get("diag_ratio") is not None:
            bits.append(f"尺寸比 {_pct(metrics['diag_ratio'])}（该组件 / 主体 AABB）")
        if metrics.get("gap_ratio") is not None:
            bits.append(f"与主体相距 {_pct(metrics['gap_ratio'])} 主体 AABB")
        bits.append("同尺寸同类件数 0")
        return " · ".join(bits)
    return f"检出入口 {row.get('entry')}"


def finding_problem_text(row: dict) -> str:
    """检出的**一句话问题陈述**：交付页"问题"列用（白话、无处置话术、无阈值术语）。

    与 `finding_reason_text` 的分工：reason 是报告里的完整判定句（带阈值与处置提示），
    problem 是回答"这个组件到底出了什么事、要不要人看"的一行话；判据与量化在
    `evidence_str` 里单独给出（改定 2026-09-23）。两者都是 findings 数字的确定性函数，
    仍属展示层——改措辞不需要重跑检出。
    """
    m = row.get("metrics") or {}
    ev = row.get("evidence") or {}
    nb = ev.get("neighbor_rank")
    nb_txt = f"组件 #{nb}" if nb is not None else "另一组件"
    if row["defect_class"] == CLASS_CO_LOCATED:
        frac = m.get("overlap_frac")
        pct = f"{frac:.0%}" if frac is not None else "部分"
        subtype = ev.get("co_located_subtype")
        if subtype == COLoc_OVERLAP:
            return (f"与{nb_txt} 有 {pct} 的表面积重叠——两套面叠在同一位置，"
                    "从不同角度看会来回闪烁")
        if subtype == COLoc_PARTIAL:
            return (f"与{nb_txt} 重叠 {pct}——可能只是拆件的正常贴合，"
                    "也可能是真重叠，需要目检确认")
        return f"与{nb_txt} 沿分界面对接（{pct} 面积重合）——拆件的正常形态，不是问题"
    if row["defect_class"] == CLASS_FLOATING:
        shards = m.get("island_shards") or 1
        gap = m.get("gap_ratio")
        gap_txt = f"，最近处相距整体 AABB 对角线的 {gap:.1%}" if gap is not None else ""
        if shards > 1:
            return (f"整组悬浮：它和另外 {shards - 1} 个组件一起脱离主体{gap_txt}"
                    "——若这些是同一件设计物的组成部分（如一双鞋），可判为非缺陷")
        return f"悬浮碎片：与主体完全分离{gap_txt}"
    if row["defect_class"] == CLASS_OTHER:
        diag = m.get("diag_ratio")
        size_txt = (f"尺寸约为主体 AABB 对角线的 {diag:.0%}"
                    if diag is not None else "尺寸很小")
        return (f"疑似碎屑（低优先）：它紧贴主体、{size_txt}，也没有尺寸同类的设计件"
                "——也可能就是设计件本身，需要人工确认")
    return row.get("reason", "")


def v2_conclusion(n_pieces: int, counts: dict[str, int]) -> str:
    """v2 卡片结论句：组件总数 → 待复核构成 → 部件贴合界面（导出特性）单列。

    `n_pieces` **必须传几何拆分（trimesh）口径的组件数**，与明细表的 `#rank` 同源——
    传 bpy 焊接口径会出现"结论 8 件、表里 #10"（`blender_checks.n_pieces` 是另一口径）。

    `counts` **必须传 `findings.count_for_display` 的结果**（三类问题只数待复核档、
    贴合界面只数 keep 档）。旧版直接传按类计数，会把同一批界面贴合行同时算进
    "组件重叠"与"贴合界面"——实测 p15 页面因此写"重叠面 48 个"，而真正待复核的
    共位只有 30 条，与页头"待复核检出 31 条"自相矛盾。

    结构说明（2026-09-23 改）：此前 `n_pieces <= 1` 走提前 return，于是**连"部件贴合界面
    N 处"也一起被吞掉**（盒子里有、结论句没有）。现在总是先给件数框架、再拼各构成项。
    """
    parts, gloss = [], []
    if counts[CLASS_FLOATING]:
        parts.append(f"悬浮 {counts[CLASS_FLOATING]} 条")
        gloss.append("悬浮是整块脱离主体、悬在空白处")
    if counts[CLASS_CO_LOCATED]:
        parts.append(f"组件重叠 {counts[CLASS_CO_LOCATED]} 条")
        gloss.append("重叠是两套面叠在同一位置、从不同角度看会来回闪烁")
    if counts[CLASS_OTHER]:
        parts.append(f"疑似碎屑 {counts[CLASS_OTHER]} 条（低优先）")
        gloss.append("疑似碎屑是紧贴主体、与设计件难以区分的小块")
    total = counts.get("review_total", sum(
        counts[c] for c in (CLASS_FLOATING, CLASS_CO_LOCATED, CLASS_OTHER)))

    if n_pieces > 1:
        concl = f"本模型共 {n_pieces} 个组件（几何拆分口径，编号按面数降序，与下表同源）。"
    else:
        concl = "单一组件模型（悬浮与重叠都需要至少两个组件）。"
    if parts:
        # 只解释本模型实际存在的问题类别；名词解释放在清单之后单起一句，避免括号套括号
        concl += (f"待人工复核 {total} 条——" + "、".join(parts)
                  + "。其中：" + "；".join(gloss) + "。")
    elif n_pieces > 1:
        concl += "未检出待人工复核的问题。"
    if counts.get("interface"):
        concl += (f"另有部件贴合界面 {counts['interface']} 处——多组件拆分时对接处的顶点"
                  "被两件共用，属拆件导出的正常形态（不计入复核队列），不是缺陷。")
    return concl


def load_detections(data_dir: Path, keys: list[str]) -> dict | None:
    """findings.jsonl → v2 检出汇总结构（无记录返回 None = legacy 版式）。

    共位类按 tier 拆开：review（重叠型 + 灰区）进检出计数与复核表；keep（界面
    贴合，非缺陷）转为批次级"导出特性"度量，不占复核队列（2026-09-22 分型）。
    """
    if not (data_dir / "findings.jsonl").is_file():
        return None
    keyset = set(keys)
    all_rows = [r for r in read_findings(data_dir / "findings.jsonl")
                if r["key"] in keyset]
    if not all_rows:
        return None
    rows = [r for r in all_rows
            if not (r["defect_class"] == CLASS_CO_LOCATED
                    and r["tier"] == "keep")]
    interface_rows = [r for r in all_rows
                      if r["defect_class"] == CLASS_CO_LOCATED
                      and r["tier"] == "keep"]
    by_key: dict[str, list[dict]] = {}
    for r in rows:
        by_key.setdefault(r["key"], []).append(r)
    interface_by_key: dict[str, list[dict]] = {}
    for r in interface_rows:
        interface_by_key.setdefault(r["key"], []).append(r)
    groups = {}
    for cls in CLASS_ORDER:
        cls_rows = [r for r in rows if r["defect_class"] == cls]
        models = []
        for key in sorted({r["key"] for r in cls_rows}):
            sub = sorted((r for r in cls_rows if r["key"] == key),
                         key=lambda r: (-(r.get("n_faces") or 0), r["rank"]))
            models.append({
                "key": key,
                "rows": [{**r, "evidence_str": evidence_str(r),
                          "reason": finding_reason_text(r)} for r in sub],
                "image": "", "sheet": "",
            })
        groups[cls] = {"label": CLASS_LABEL[cls], "total": len(cls_rows),
                       "models": models}
    summary = summarize(rows)
    summary["interface"] = {"findings": len(interface_rows),
                            "models": len({r["key"] for r in interface_rows})}
    det = {"summary": summary, "groups": groups,
           "class_order": list(CLASS_ORDER), "by_key": by_key,
           "interface_by_key": interface_by_key}
    other_total = groups[CLASS_OTHER]["total"]
    if other_total:
        det["noncore_note"] = (f"另有 {other_total} 条非核心类检出（贴合且非共位、非悬浮，"
                               "几何上与设计件不可分）：保留检出，标注低优先人工，不与两类核心混排。")
    return det


def embed(path: Path, max_width: int = EMBED_MAX_WIDTH) -> str:
    """读取 PNG → 降采样 → base64。缺失文件返回空串（模板留白可见）。

    max_width 按图的【显示尺寸】决定（可读性修复 2026-09-21）：统一 420 会把
    显示宽 >420 的图（拼图/对比图）先压糊再拉伸。
    """
    if not path.is_file():
        return ""
    try:
        from PIL import Image

        img = Image.open(path)
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, max(1, int(img.height * ratio))))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return base64.b64encode(path.read_bytes()).decode("ascii")


def _trio(render_dir: Path, prefix: str) -> list[str]:
    return [embed(render_dir / f"{prefix}{v}.png") for v in GRID]


def build_card(key: str, flag: dict, met: dict, task: dict, raw: Path,
               fixed_dir: Path, pv: dict | None = None,
               disposition: str | None = None,
               detect_counts: dict | None = None,
               conclusion: str | None = None) -> dict:
    ba = fixed_dir / "before_after.json"
    fix_diff = json.loads(ba.read_text(encoding="utf-8")) if ba.is_file() else None
    fixed = fix_diff is not None
    after_dir = fixed_dir / "render" if fixed else raw / "render"
    meta_path = raw / "meta.json"
    category = json.loads(meta_path.read_text(encoding="utf-8")).get("category", "") \
        if meta_path.is_file() else ""
    # T2 批次：卡片展示四档判定与部件判别计数；all/standard 保持原卡片不变
    if detect_counts is not None:
        total = sum(detect_counts.values())
        verdict = "待复核" if total else "无检出"
    else:
        verdict = disposition or flag["verdict"]
    card = {
        "key": key,
        "prompt": task.get("prompt") or key,
        "category": category,
        "verdict": verdict,
        "verdict_cls": VERDICT_CLS.get(verdict, "direct"),
        "n_faces": met.get("n_faces"), "n_pieces": flag["n_pieces"],
        "n_small_pieces": flag["n_small_pieces"],
        "fragment_face_ratio": flag["fragment_face_ratio"],
        "non_manifold_edges": flag["non_manifold_edges"],
        "watertight": met.get("watertight"),
        "credits": task.get("credits"),
        "thumb": embed(raw / "render" / "base_iso.png"),
        "fixed": fixed,
        "fix_diff": fix_diff,
        "before_base": _trio(raw / "render", "base_"),
        "before_hl": _trio(raw / "render", "highlight_"),
        "after_base": _trio(after_dir, "base_"),
        "after_wire": _trio(after_dir, "wire_"),
        "compare": embed(after_dir / "compare_front.png", max_width=1200),
    }
    if detect_counts is not None:
        card["detect_counts"] = detect_counts
        card["conclusion"] = conclusion
        nm = flag.get("non_manifold_edges") or 0
        if nm > 0:
            card["nm_note"] = (f"另检出 {nm} 条非流形边（另一类几何缺陷，与悬浮组件无关）："
                               "仅检出，当前版本不自动修复。")
        return card
    if pv is not None:
        counts = pv.get("counts", {})
        card["part_counts"] = counts
        n_pieces = flag["n_pieces"]
        a, c = counts.get("A", 0), counts.get("C", 0)
        parts_kept = counts.get("B", 0) - 1  # 主体占一个 B
        if n_pieces <= 1:
            concl = "单一组件模型：未检出任何独立碎片或待审组件。"
        else:
            segs = []
            if a:
                segs.append(f"{a} 个判定为伪影（自动删除候选，着红色）")
            if c:
                segs.append(f"{c} 个待人工复核（见下方放大拼图与批次复核表）")
            if parts_kept > 0:
                segs.append(f"{parts_kept} 个判定为设计部件（保留）")
            concl = f"检出 {n_pieces} 个独立组件：" + "、".join(segs) + "。"
            if a == 0 and c > 0:
                concl += "无自动删除项。"
        card["conclusion"] = concl
        nm = flag.get("non_manifold_edges") or 0
        if nm > 0:
            card["nm_note"] = (f"另检出 {nm} 条非流形边（另一类几何缺陷，与悬浮组件无关）："
                               "仅检出，当前版本不自动修复。")
        ratio = flag.get("fragment_face_ratio") or 0
        threshold = flag.get("thresholds", {}).get("frag_reject_ratio")
        if ratio and threshold and ratio > threshold:
            card["legacy_note"] = (f"legacy 信号：{ratio:.1%} 的面面积位于尺寸不足主体 1/10 的"
                                   f"组件里，超过 standard 批次红线（{threshold}）。该红线假设"
                                   f"主体占模型绝大部分面积，在 T2 原生多组件形态下天然偏高，"
                                   f"故不作为本批次档位依据。")
    return card


def build_summary(data_dir: Path = DATA_DIR, batch: str = "all") -> dict:
    cfg = load_config()
    t2 = batch == T2_PRESET
    tasks: dict[str, dict] = {}
    tasks_path = data_dir / "tasks.json"
    if tasks_path.exists():
        for rec in json.loads(tasks_path.read_text(encoding="utf-8")):
            tasks[rec["key"]] = rec

    metrics = {}
    metrics_path = data_dir / "metrics.jsonl"
    if metrics_path.exists():
        for rec in read_jsonl(metrics_path):
                metrics[rec["key"]] = rec

    flags = {}
    flags_path = data_dir / "flags.jsonl"
    if flags_path.exists():
        for rec in read_jsonl(flags_path):
                flags[rec["key"]] = rec

    # 批次过滤（D9：T2 与 standard 两档不合并）；all = 不过滤（冻结归档口径）
    keys = sorted(flags)
    if batch == T2_PRESET:
        keys = [k for k in keys if k.endswith("@" + T2_PRESET)]
    elif batch == "standard":
        keys = [k for k in keys if k.endswith("@standard")]
    pv_map = load_part_verdicts(data_dir) if t2 else {}

    detections = load_detections(data_dir, keys)
    det_by_key = detections["by_key"] if detections else {}
    iface_by_key = (detections or {}).get("interface_by_key", {})

    cards = []
    funnel = ({"total": 0, "direct": 0, "fix": 0, "review": 0, "reject": 0} if t2
              else {"total": 0, "direct": 0, "fix": 0, "reject": 0})
    crosscheck = []
    a_rows: list[dict] = []
    c_groups: list[dict] = []
    for key in keys:
        flag = flags[key]
        pv = pv_map.get(key)
        disp = disposition_of(flag, pv)
        met = metrics.get(key, {})
        task = tasks.get(key, {})
        raw = data_dir / "raw" / key
        fixed_dir = data_dir / "fixed" / key
        funnel["total"] += 1
        if not detections:
            if t2:
                funnel[VERDICT_CLS[disp]] += 1
            elif flag["verdict"] == "直接入库":
                funnel["direct"] += 1
            elif flag["verdict"] == "修复后入库":
                funnel["fix"] += 1
            else:
                funnel["reject"] += 1
        det_counts = None
        concl = None
        if detections:
            rows = det_by_key.get(key, [])
            det_counts = count_by_class(rows)
            det_counts["interface"] = len(iface_by_key.get(key, []))
            concl = v2_conclusion(flag["n_pieces"], det_counts)
        cards.append(build_card(key, flag, met, task, raw, fixed_dir,
                                pv=pv if (t2 and not detections) else None,
                                disposition=disp if (t2 and not detections) else None,
                                detect_counts=det_counts, conclusion=concl))
        if t2 and pv and not detections:
            for d in pv.get("decisions", []):
                if d.get("tier") == TIER_FIX_ELIGIBLE:
                    a_rows.append({"key": key, **d})
            # 复核顺序：入口叙事序（真实性门 → 未取证 → 疑似位移 → 不可分），
            # 同组内按面数降序（大的先看，信息量最高）
            entry_order = {"A0": 0, "A1-off": 1, "C1": 2, "C2": 3}
            c_rows = sorted(
                ({**d, "entry_label": ENTRY_LABEL.get(d.get("entry"), d.get("entry"))}
                 for d in pv.get("decisions", [])
                 if d.get("tier") == TIER_REVIEW),
                key=lambda d: (entry_order.get(d.get("entry"), 9),
                               -(d.get("n_faces") or 0)))
            if c_rows:
                # 组件配色总览与逐个放大拼图已随 PLAN-07 §五 撤销（每件一色在组件多时
                # 物理上不成立；定位改由逐检出定位图承担）
                c_groups.append({"key": key, "rows": c_rows})
        if "n_pieces" in met:
            checks_file = raw / "blender_checks.json"
            bchecks = json.loads(checks_file.read_text(encoding="utf-8")) \
                if checks_file.is_file() else {}
            crosscheck.append({
                "key": key,
                "pieces_bpy": bchecks.get("n_pieces", "-"),
                "pieces_trimesh": met.get("n_pieces"),
                "nm_bpy": bchecks.get("non_manifold_edges", "-"),
                "nm_trimesh": met.get("non_manifold_edges"),
            })

    checks_file = data_dir / "raw" / keys[0] / "blender_checks.json" if keys else None
    blender_version = json.loads(checks_file.read_text(encoding="utf-8")) \
        .get("blender_version", "") if checks_file and checks_file.is_file() else ""
    summary = {
        "t2": t2,
        "batch": batch,
        "funnel": funnel,
        "thresholds": {k: cfg["detect"][k] for k in
                       ("diag_ratio", "vol_ratio", "frag_reject_ratio")},
        "engines": {"blender": blender_version},
        "cards": cards,
        "crosscheck": crosscheck,
    }
    if detections:
        # 分组复核表的图在此统一内嵌（build_summary 拿得到 raw 目录）
        for cls in CLASS_ORDER:
            for m in detections["groups"][cls]["models"]:
                raw = data_dir / "raw" / m["key"]
                m["image"] = ""
        summary["detections"] = detections
    if t2 and not detections:
        summary["parts"] = {"a_rows": a_rows, "c_groups": c_groups,
                            "c2_count": sum(1 for g in c_groups for r in g["rows"]
                                            if r.get("entry") == "C2")}
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 report.html + summary.json")
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--batch", choices=("all", T2_PRESET, "standard"), default="all",
                    help="all=全部模型（冻结归档口径）；smart-topology=T2 批次报告；"
                         "standard=standard 对照批（v2 检出口径，独立产物）")
    ap.add_argument("--out", type=Path, default=None,
                    help="输出 HTML 路径（默认 all→report.html，T2→report_t2.html，"
                         "standard→report-standard-v2.html）")
    args = ap.parse_args(argv)

    summary = build_summary(args.data_dir, batch=args.batch)
    t2 = args.batch == T2_PRESET
    default_html = {T2_PRESET: DATA_DIR / "report_t2.html",
                    "standard": DATA_DIR / "report-standard-v2.html"}.get(
        args.batch, REPORT_HTML)
    default_summary = {T2_PRESET: DATA_DIR / "summary_t2.json",
                       "standard": DATA_DIR / "summary-standard-v2.json"}.get(
        args.batch, SUMMARY_JSON)
    out_html = args.out or default_html
    out_summary = out_html.parent / (default_summary.name if not args.out
                                     else out_html.stem + ".json")
    args.data_dir.mkdir(parents=True, exist_ok=True)
    html = Environment().from_string(TEMPLATE).render(**summary)
    out_html.write_text(html, encoding="utf-8", newline="\n")
    # summary 面向快速全局阅读（LLM/人），剥离全部图片字段（计数留着，非图片）
    strip = ("thumb", "before_base", "before_hl",
             "after_base", "after_wire", "compare")
    slim = {**summary,
            "cards": [{k: v for k, v in c.items() if k not in strip}
                      for c in summary["cards"]]}
    if "detections" in summary:
        det = json.loads(json.dumps(summary["detections"]))  # 深拷贝后剥图
        det.pop("by_key", None)
        slim["detections"] = det
    if t2 and "parts" in summary:
        slim["parts"] = {
            "a_rows": summary["parts"]["a_rows"],
            "c2_count": summary["parts"]["c2_count"],
            # c_groups 里的 image/sheet（配色总览、逐个放大拼图）已随 PLAN-07 §五 撤销
            "c_groups": list(summary["parts"]["c_groups"])}
    out_summary.write_text(json.dumps(slim, ensure_ascii=False, indent=1),
                           encoding="utf-8", newline="\n")
    print(f"report: {out_html}（{len(summary['cards'])} 卡片，batch={args.batch}）"
          f" + {out_summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
