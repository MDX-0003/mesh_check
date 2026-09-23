"""06 · 交付生成器：results/<日期> 三类别目录 + 双层 HTML（PLAN-05 §二/§三）。

从 data/（findings + review + raw）生成独立于取证库的交付视图：
- 落位：review.plan_dispositions（待复核检出限定规则；人工裁决优先且不被覆盖）；
- 每模型：raw/<key> 全量 copy（glb_valid 校验）→ images/ 转码（按目的分辨率、
  按类型 JPG/PNG，见 PLAN-05 §四表）→ <key>.html 模型页；
- 队列页 index.html：四盒检出汇总 + 三类队列（缩略图/检出摘要/进入链接）；
- data/ 附录：findings.jsonl / islands.jsonl / summary*.json 随交付；
- 幂等：重跑覆盖页面与图片，人工裁决（review.jsonl）永不覆盖；
- --dry-run：打印落位与 copy 计划，写任何文件。

模型页内嵌的复核控件在审核台（meshq/tools/review_server.py）在线时可用；离线打开自动降级
为只读（fetch 失败即提示），页面本身始终完整可读。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from datetime import date
from pathlib import Path

from PIL import Image
from jinja2 import Environment

from meshq.core.common import (BATCH_SUMMARY_FILES, DATA_DIR, ROOT, glb_valid,
                                in_batch, read_jsonl)
from meshq.core.lookdev_math import PIECE_VIEWS
from meshq.core.findings import (CLASS_CO_LOCATED, CLASS_FLOATING, CLASS_OTHER,
                      count_for_display, read_findings)
from meshq.core.review import (DISPOSITION_DIR, DISPOSITION_FAIL, DISPOSITION_LABEL,
                    DISPOSITION_PASS, DISPOSITION_PENDING, load_reviews,
                    plan_dispositions, write_reviews)

RESULTS_ROOT = ROOT / "results"

# 转码规格（PLAN-05 §四）：源名 → (输出名, 格式, 目标宽度 None=原生)
WIRE_WIDTH = 1000
TRANSCODE = [
    ("base_iso.png", "thumb.jpg", "jpg", 320),
    # 干净渲染（base 通道 = 合并焊接后的整体渲染）：队列页用它做首屏观感，
    # 与模型页的判别视图是两个需求（用户 2026-09-23 口径：合并渲染即可）
    ("base_iso.png", "base.jpg", "jpg", 720),
    # 线框保持 PNG（PLAN-05 §四），但按目的分辨率降采样 + 调色板量化：
    # 原样 copy 的 1280×960 每张 1.3MB ×60 = 84MB，是交付目录里最大的一坨
    ("wire_front.png", "wire_front.png", "png", WIRE_WIDTH),
    ("wire_top.png", "wire_top.png", "png", WIRE_WIDTH),
    ("wire_right.png", "wire_right.png", "png", WIRE_WIDTH),
    ("compare_front.png", "compare.jpg", "jpg", 1200),
]

# 交付件清单（2026-09-23 收敛）：只拷"页面引用 + 模型 + 取证记录 + 逐件 GLB"。
# 不随交付复制 render/ 原始中间件——实测它占交付目录的 93.8%（1953MB / 2082MB），
# 而交付页一张都不引用（页面只引 images/ 下转码后的 jpg/png）。原始件在 data/raw/
# 原样保留，交付目录可由本脚本整体重建，故不牺牲可复现性。
# 逐件 GLB（pieces/）留下：它是 trimesh 编号的实体，PLAN-06 §2.5 的图例要按 rank 链到它。
DELIVER_FILES = ("model.glb", "blender_checks.json", "meta.json")
DELIVER_DIRS = ("pieces",)

PAGE_CSS = """
body { font-family: "Segoe UI","Microsoft YaHei",sans-serif; margin:0;
       background:#f5f6f8; color:#222; }
header { background:#2c3440; color:#fff; padding:14px 24px; }
header h1 { font-size:18px; margin:0; }
header .sub { color:#aeb8c4; font-size:13px; margin-top:4px; }
main { padding:16px 24px; max-width:1600px; margin:0 auto; }
.boxes { display:flex; gap:10px; margin:12px 0; flex-wrap:wrap; }
.boxes span { padding:8px 14px; border-radius:8px; color:#fff; font-weight:600;
              font-size:14px; }
.b-float { background:#b03a3a; } .b-coloc { background:#5b6ac5; }
.b-other { background:#d18b1f; } .b-iface { background:#66707d; }
.b-pend { background:#d18b1f; } .b-pass { background:#2e8b57; }
.b-failx { background:#b03a3a; }
.summary { font-size:15px; line-height:1.7; background:#fff; border-radius:8px;
           padding:12px 16px; box-shadow:0 1px 4px rgba(0,0,0,.06); }
h2 { font-size:16px; margin:22px 0 8px; }
table { border-collapse:collapse; font-size:13px; background:#fff;
        width:100%; table-layout:fixed; }
td,th { border:1px solid #dde; padding:4px 10px; text-align:left; }
.card { background:#fff; border-radius:10px; padding:12px 14px; margin:10px 0;
        box-shadow:0 1px 4px rgba(0,0,0,.08); }
.row { display:flex; flex-wrap:wrap; gap:8px; align-items:flex-start; }
.row img { border-radius:6px; background:#ddd; }
/* 明细表：4 列（组件编号/单件外观/定位图/问题）。图必须限宽，否则会把文字列挤成竖条
   （2026-09-23：图证补齐后就出现过每行 1~2 个字的情况）。
   尺寸策略：固定列宽（table-layout:fixed）+ 图 `max-width:100%; max-height:150px`，
   于是宽屏下图按 150px 高显示（组件独立渲染图 4:1 → 约 600px，定位图双机位 2.67:1 →
   约 400px），窄屏下按列宽等比缩小而不是把表格撑出横向滚动条。 */
table col.c-rank { width:72px; }
table col.c-crop { width:38%; }
table col.c-loc { width:26%; }
table td { vertical-align:top; overflow-wrap:anywhere; }
table th { white-space:nowrap; }
table td img { max-width:100%; max-height:150px; width:auto; height:auto;
               display:block; border-radius:4px; background:#ddd; }
table td .meta { margin-top:4px; color:#7a818c; }
.review-card { border-left:4px solid #d18b1f; }
.meta { color:#666; font-size:13px; }
a { color:#3a5fcd; text-decoration:none; }
.badge { display:inline-block; border-radius:4px; padding:1px 8px; font-size:12px;
         background:#eef1f5; margin-right:6px; }
.disp-pass { color:#2e8b57; font-weight:700; }
.disp-fail { color:#b03a3a; font-weight:700; }
.disp-pending { color:#5b6ac5; font-weight:700; }
details { margin:8px 0; }
button { padding:4px 12px; margin-right:6px; border:1px solid #99a; border-radius:6px;
         background:#fff; cursor:pointer; }
button.on { background:#2c3440; color:#fff; }
input[type=text] { width:360px; padding:4px 8px; }
"""

# 模型页模板：图证相对引用 images/；复核控件 POST /api/disposition（离线自动只读）
MODEL_PAGE = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>{{ key }} · 检出明细</title>
<link rel="icon" href="data:,">
<style>{{ css }}</style></head><body>
<header><h1>{{ key }} <span class="badge" style="background:#465">{{ category_label }}</span></h1>
<div class="sub">对象：{% if object.category %}<b>{{ object.category }}</b> · {% endif %}"{{ object.object }}"{% if object.float_inducing %} <span class="badge">悬浮诱导 prompt</span>{% endif %}</div>
<div class="sub">待复核检出 {{ counts.review_total }} 条 ·
<a style="color:#9fc" href="../../index.html">← 返回队列</a></div></header>
<main>
<div class="card" style="border-left:4px solid #5b6ac5">
  <b>检测结论</b>　{{ conclusion}}
  <div class="meta" style="margin-top:6px">口径与来源：数字来自<b>几何拆分（trimesh）</b>口径的检出结果；
    图由 <b>Blender 5.0.1 headless 渲染</b>。{{ engine_note }}</div>
</div>
<div class="boxes">
  <span class="b-float">悬浮组件 {{ counts.floating }}</span>
  <span class="b-coloc">组件重叠 {{ counts.co_located }}</span>
  <span class="b-other">疑似碎屑（低优先）{{ counts.other }}</span>
  <span class="b-iface">部件贴合界面（正常形态）{{ counts.interface }}</span>
</div>

<!-- 入库裁决：整模型一个结论（2026-09-23 提到首屏，按用户口径不做逐条控件） -->
<div class="card review-card" id="review-card">
  <div class="row" style="align-items:center;gap:14px">
    <div style="flex:1">
      <b>入库裁决</b>　<span class="disp-{{ plan_disposition }}">{{ disposition_label }}</span>
      <div class="meta" style="margin-top:4px">{{ disposition_reason }}{% if reviewed_at and reviewed_at != "—" %}
        · 复核人 {{ reviewed_by }} @ {{ reviewed_at }}{% endif %}</div>
    </div>
    <div id="review-box" data-key="{{ key }}">
      <button data-d="pass">入库</button>
      <button data-d="fail">不入库</button>
      <button data-d="pending">维持待定</button>
      <input type="text" id="review-reason" placeholder="裁决理由（可选）">
    </div>
  </div>
  <div class="meta" id="review-msg"></div>
</div>

<h2>{{ detail_title }}（{{ review_rows|length }} 条待人工复核）</h2>
<p class="meta">{{ detail_caption }}</p>
{% if review_rows %}
<table><colgroup><col class="c-rank"><col class="c-crop"><col class="c-loc"><col class="c-prob"></colgroup>
<tr><th>组件编号</th><th>单件外观</th><th>定位图</th><th>问题</th></tr>
{% for r in review_rows %}<tr>
<td>#{{ r.rank }}<div class="meta">{{ r.n_faces }} 面</div></td>
<td>{% if r.crop %}<img src="images/{{ r.crop }}">{% else %}—{% endif %}</td>
<td>{% if r.locator %}<img src="images/{{ r.locator }}">{% else %}—{% endif %}</td>
<td>{{ r.problem }}{% if r.evidence_str %}<div class="meta">{{ r.evidence_str }}</div>{% endif %}</td>
</tr>{% endfor %}</table>
<p class="meta">怎么读这两列图——<b>组件独立渲染</b>（表头「单件外观」列）：把该组件单独拿出来渲的三视图，回答"它长什么样"；
<b>定位图</b>：左面板给出它在整机里的位置（灰底是其余组件，半透明；<span style="color:#c0266e">环</span>圈出该件），
右面板贴近看它与邻居的关系。定位图配色：<span style="color:#b8860b">● 琥珀</span> = 该件未重合的部分；
<span style="color:#c0266e">● 品红</span> = 与邻居表面在容差内贴合（重合）的那一片；
<span style="color:#1f8a9c">● 青</span> = 邻居组件。<b>极小件例外</b>：整机面板里可能只有几个像素，
此时以左侧的组件独立渲染为准。</p>
{% else %}<p class="meta">本模型无待复核检出。</p>{% endif %}
<details><summary style="cursor:pointer">结构核实 · 线框三视图（可辨三角面密度）{% if has_compare %} / 修复前后对比{% endif %}</summary>
<div class="row">
{% for img in wire_images %}<img src="images/{{ img }}" style="width:410px">{% endfor %}
{% if has_compare %}<img src="images/compare.jpg" style="width:100%;max-width:1200px">{% endif %}
</div>
</details>
<script>
document.querySelectorAll("#review-box button").forEach(b => b.onclick = async () => {
  const msg = document.getElementById("review-msg");
  try {
    const resp = await fetch("/api/disposition", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({key: document.getElementById("review-box").dataset.key,
        disposition: b.dataset.d,
        reason: document.getElementById("review-reason").value})});
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    msg.textContent = "已提交，队列刷新中…";
    setTimeout(() => location.href = "../../index.html", 800);
  } catch (e) {
    msg.textContent = "当前为离线快照（只读）：裁决请在本机运行 python -m meshq.tools.review_server";
  }
});
</script>
</main></body></html>
"""

INDEX_PAGE = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>质检交付队列 · {{ date }}</title>
<link rel="icon" href="data:,">
<style>{{ css }}</style></head><body>
<header><h1>生成式 3D 资产几何质检 · 交付队列</h1>
<div class="sub">{{ subtitle }}</div></header>
<main>
<div class="boxes">
  <span class="b-pend">待人工裁决 {{ counts.pending }} 个</span>
  <span class="b-pass">确定通过 {{ counts.pass }} 个</span>
  <span class="b-failx">确定不通过 {{ counts.fail }} 个</span>
</div>
<p class="summary">{{ problem_summary }}</p>
<p class="meta">{{ interface_note }} 落位规则：<b>无待复核检出 → 确定通过；
有待复核检出 → 待人工裁决；确定不通过仅由人工裁决产生</b>（部件贴合界面不计入
落位）。全部检出只报告、不自动修改；每条检出带图证、判据与白话理由（见各模型页
的「问题组件」表），人工可直接推翻。模型页首屏有「入库裁决」按钮。</p>
<p class="meta">方法与来源：几何判定由 <b>trimesh</b> 口径的检出管线给出
（组件划分 / 视觉岛聚类 / 面积重叠率），渲染图由 <b>Blender 5.0.1 headless 渲染</b>；
两套引擎并行互证；每个模型页的「口径与来源」行给出该模型的组件数在两套口径下的差异。</p>
{% for disp in order %}
<h2>{{ labels[disp] }}（{{ queues[disp]|length }}）</h2>
{% for m in queues[disp] %}
<div class="card row">
  {% if m.base %}<img src="{{ m.link }}images/base.jpg" style="height:150px">{% endif %}
  <div style="flex:1">
    <b><a href="{{ m.link }}{{ m.key }}.html">{{ m.key }}</a></b>
    <span class="badge">待复核 {{ m.review_count }}</span>
    {% if m.interface_count %}<span class="badge">贴合界面 {{ m.interface_count }}</span>{% endif %}
    {% if m.object.float_inducing %}<span class="badge">悬浮诱导 prompt</span>{% endif %}
    <div class="meta">{% if m.object.category %}{{ m.object.category }} · {% endif %}"{{ m.object.object }}"</div>
    <div class="meta">{{ m.composition }}</div>
  </div>
</div>
{% endfor %}
{% if not queues[disp] %}<p class="meta">（空）</p>{% endif %}
{% endfor %}
<p class="meta">可核对的数据附录：<code>data/findings.jsonl</code>（每条检出）、
<code>data/islands.jsonl</code>（视觉岛）、<code>data/prompts.jsonl</code>（prompt 定义）·
每模型目录下另有 <code>blender_checks.json</code>（Blender 口径原生指标）。</p>
<p class="meta">本页由本仓库的交付脚本 <code>meshq/stages/deliver.py</code> 从上述数据文件生成；
离线打开时裁决控件为只读，本机运行 <code>python -m meshq.tools.review_server</code> 即可在线裁决。</p>
</main></body></html>
"""


def _hstrip(imgs):
    """等高横向拼接多张 PIL 图（单引擎件的组件独立渲染三视图）。"""
    h = min(i.height for i in imgs)
    imgs = [i.resize((max(1, int(i.width * h / i.height)), h)) for i in imgs]
    w = sum(i.width for i in imgs) + 8 * (len(imgs) - 1)
    canvas = Image.new("RGB", (w, h), (28, 30, 34))
    x = 0
    for i in imgs:
        canvas.paste(i, (x, 0))
        x += i.width + 8
    return canvas


def _transcode(src: Path, dest: Path, fmt: str, width: int | None,
               force: bool = False) -> bool:
    """按规格转码一张图；源缺失返回 False（调用方保留既有图 / 页面留占位）。

    跳过判据**必须先查源、再比 mtime**：反过来的话，源缺失而目标已存在时 `src.stat()`
    会先抛 `FileNotFoundError`（`dest.is_file()` 为真时短路保护失效），与"源缺失返回 False"
    的承诺直接矛盾。触发场景很现实：交付目录里已有图 + 某个渲染源缺失（例如清理/误删了
    `render/` 中间件，只要涉及 `base_iso.png` / `wire_*.png`），下一次 deliver 会当场崩，
    而不是优雅跳过（2026-09-23 实测复现并修复）。

    目标已存在且不旧于源时跳过（P3：重生成只处理新增/更新的图，秒级增量）。
    `force=True` 绕过该跳过——**转码规格本身变了**（改宽度、改编码）时 mtime 看不出来，
    不绕过就会静默留下一批按旧规格生成的图（见 `_spec_signature`）。
    """
    if not src.is_file():
        return False
    if not force and dest.is_file() and dest.stat().st_mtime >= src.stat().st_mtime:
        return True
    from PIL import Image
    img = Image.open(src)
    if width and img.width > width:
        img = img.resize((width, max(1, int(img.height * width / img.width))))
    dest.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "png":
        # 线框在深底 lookdev 上近似灰阶，调色板量化 + 降采样足以压到 ~1/10，
        # 页面按 410px 显示，1000px 源绰绰有余（PLAN-05 §四"按目的分辨率"）
        img.convert("RGB").quantize(colors=256, method=Image.MEDIANCUT).save(
            dest, format="PNG", optimize=True)
        return True
    img.convert("RGB").save(dest, format="JPEG", quality=88, optimize=True)
    return True


# 逐检出合成图的造型指纹（组件独立渲染图 / 定位图横拼）：**拼法或视图集一变就必须换值**，
# 否则旧交付里的同名文件会留下——而且 mtime 判据（dest 不比源旧就跳过）恰恰会给它们
# 发"无需重做"的通行证。2026-09-23 实测：256 张 crop_<rank>.jpg 里 45 张仍是上一代
# "主场景高亮裁剪"的产物（15×51、1280×960 这类尺寸），它们比源视图新，普通重跑修不掉。
COMPOSITE_SPEC = {
    "crop": f"hstrip:{'+'.join(PIECE_VIEWS)}",
    "locator": "hstrip:model+close+ring",
}


def _spec_signature() -> str:
    """规格指纹：转码规格或逐检出合成图造型变了，就必须忽略 mtime 跳过。

    触发场景：把线框从"原样 copy"改成"降采样+量化"、改任何目标宽度、改拼法或
    视图集。此时 `dest` 比 `src` 新（上次刚做过），mtime 判据会误判为"无需重做"。
    两类产物共用一个指纹：它们都在 `results/<日期>/images/`，且同属"造型依赖代码、
    不依赖源文件 mtime"的一类，分开记反而会出现只强刷其中一半的漏网情况。
    """
    import hashlib
    return hashlib.sha256(
        json.dumps({"transcode": TRANSCODE, "composite": COMPOSITE_SPEC},
                   ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


LOCATOR_RING = (214, 30, 110)      # 标记环颜色（与品红同族，醒目不抢戏）
LOCATOR_RING_MIN_R = 13            # 最小环半径（px）：组件再小也圈得住


def _compose_locator(render_dir: Path, rank: int, marks: dict) -> "object | None":
    """`locator_<rank>_{model,close}.png` → 一张横拼图，并在整机面板上画标记环。

    两个机位缺一不可：整机面板回答"在哪"，贴近面板回答"叠的是哪一片、占多少"。
    整机取景下小组件可能只有几像素（p01#8 约 4 px），故按 `locator_marks.json` 记录
    的投影像素框画一个固定最小半径的环，保证找得到。
    """
    from PIL import Image as _Image
    from PIL import ImageDraw
    a = render_dir / f"locator_{rank}_model.png"
    b = render_dir / f"locator_{rank}_close.png"
    if not a.is_file() or not b.is_file():
        return None
    canvas = _hstrip([_Image.open(a).convert("RGB"), _Image.open(b).convert("RGB")])
    m = (marks.get(str(rank)) or {}).get("model")
    if m and len(m) == 4:
        d = ImageDraw.Draw(canvas)
        cx, cy = (m[0] + m[2]) / 2.0, (m[1] + m[3]) / 2.0
        r = max(LOCATOR_RING_MIN_R,
                (((m[2] - m[0]) ** 2 + (m[3] - m[1]) ** 2) ** 0.5) / 2.0 + 6.0)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=LOCATOR_RING, width=3)
    return canvas


def _load_report_mod():
    """报告模块：结论口径（`v2_conclusion`）由 report 承担，交付层不复制一份。

    去编号前 `05_report.py`（现 report.py）无法常规 import，只能按路径动态加载；现在直接 import。
    """
    import meshq.stages.report as report
    return report


def _model_view(rows: list[dict], report_mod) -> tuple[list[dict], int]:
    """findings 行 → (复核表行, 界面贴合计数)；问题陈述与技术旁注复用 05 的白话口径。"""
    review_rows, interface = [], 0
    for r in sorted(rows, key=lambda x: (x["defect_class"], -(x.get("n_faces") or 0))):
        keep_interface = (r["defect_class"] == CLASS_CO_LOCATED
                          and r["tier"] == "keep")
        if keep_interface:
            interface += 1
            continue
        review_rows.append({**r, "problem": report_mod.finding_problem_text(r),
                            "evidence_str": report_mod.evidence_str(r)})
    return review_rows, interface


def _engine_note(dest: Path, n_pieces: int | None) -> str:
    """模型页的"两套引擎口径差异"一行（只在两者不同时出现）。

    取代此前逐行重复的"另一划分口径下未找到对应件"（那在 88% 的共位行上都印一遍，
    读者无从分辨）。这里只讲一次、讲清是哪两个数、为什么不同。
    """
    checks = dest / "blender_checks.json"
    if not checks.is_file() or not n_pieces:
        return ""
    try:
        bpy_pieces = int(json.loads(checks.read_text(encoding="utf-8")).get("n_pieces") or 0)
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    if not bpy_pieces or bpy_pieces == n_pieces:
        return ""
    return (f"另：同一模型在 Blender 焊接口径下为 {bpy_pieces} 个组件（与几何拆分口径的 "
            f"{n_pieces} 个不同）——两套引擎的焊接算法不同（邻近合并 vs 量化去重），"
            "细碎片的归并结果因此有差异，不是错误；编号与图证一律取几何拆分口径。")


def _piece_count(dest: Path) -> int | None:
    """组件数：**几何拆分（trimesh）口径**，页面编号的唯一来源。

    取交付目录里的 `pieces/manifest.json`（按清单拷贝后随模型走）。不再用
    `blender_checks.n_pieces`——那是 bpy 焊接口径，与表格的 `#rank` 不同源，
    会出现"结论 8 个组件、表里 #10"（PLAN-06 §2.4.4）。缺失时返回 None，
    由调用方回退并标注口径。
    """
    mf = dest / "pieces" / "manifest.json"
    if not mf.is_file():
        return None
    try:
        return int(json.loads(mf.read_text(encoding="utf-8")).get("n_pieces"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def render_model_page(key: str, dest: Path, rows: list[dict], plan: dict,
                      report_mod) -> None:
    """渲染单模型页 + 裁决镜像（图片已在 images/，按存在性引用）。

    独立成函数供审核台在裁决后只重渲染受影响模型（亚秒级，不重转码）。
    """
    # 展示口径：三类"问题"只数待复核档，贴合界面只数 keep 档（见 findings.count_for_display）
    counts = count_for_display(rows)
    review_rows, interface_count = _model_view(rows, report_mod)
    images = dest / "images"
    for r in review_rows:
        # 逐检出图证文件名契约：deliver_one 写 crop_<rank>.jpg（单引擎件的组件独立渲染）；
        # crop_<rank>_*.jpg 为多图形态的向后兼容读取（历史上曾按视图拆分命名）。
        cands = (sorted(images.glob(f"crop_{r['rank']}.jpg"))
                 or sorted(images.glob(f"crop_{r['rank']}_*.jpg")))
        r["crop"] = cands[0].name if cands else None
        loc = images / f"locator_{r['rank']}.jpg"
        r["locator"] = loc.name if loc.is_file() else None
    dest.mkdir(parents=True, exist_ok=True)
    only_overlap = bool(review_rows) and all(
        r["defect_class"] == CLASS_CO_LOCATED for r in review_rows)
    # 说明只讲"编号怎么看、表是什么"——不再承诺逐条动作（表里没有逐条控件，
    # 承诺会让人找不到入口；入库裁决是整模型一个按钮，在首屏裁决卡上）
    head = ("编号 = 组件在几何拆分（trimesh）口径下的序号，按面数降序、0 为最大件，"
            "与组件独立渲染图、逐件 GLB 文件名同源。"
            "本表只报告不修改——删 / 留的决定在首屏「入库裁决」卡上按模型给出。")
    if only_overlap:
        detail_title = "问题组件（重叠）"
        detail_caption = head
    else:
        detail_title = "问题组件"
        detail_caption = (head + "问题分三类：与其他组件有表面积重叠（渲染时来回闪烁）、"
                          "整块脱离主体悬浮、疑似碎屑（紧贴主体且与设计件难区分，低优先）。"
                          "表中百分比的分母：悬浮类的距离与阈值取<b>整体 AABB 对角线</b>"
                          "（整个模型外接长方体的对角线）；疑似碎屑的尺寸比与间隙取"
                          "<b>主体 AABB 对角线</b>（最大组件的对应值）；重叠率是表面积之比，"
                          "与尺寸无关。")
    # 结论句件数取几何拆分（trimesh）口径，与表格 #rank 同源（PLAN-06 §2.4.4）
    n_pieces = _piece_count(dest)
    if n_pieces is None:
        # 缺拆分产物（旧交付目录/未建 store）：回退 bpy 口径并在结论句标明，不静默混淆
        checks_path = dest / "blender_checks.json"
        n_pieces = int(json.loads(checks_path.read_text(encoding="utf-8")
                                  ).get("n_pieces") or 0) if checks_path.is_file() else 0
        conclusion = (report_mod.v2_conclusion(n_pieces, counts)
                      + "（组件数为 bpy 口径回退值：本模型缺拆分产物）")
    else:
        conclusion = report_mod.v2_conclusion(n_pieces, counts)
    html = Environment().from_string(MODEL_PAGE).render(
        css=PAGE_CSS, key=key, counts=counts,
        category_label=DISPOSITION_LABEL[plan["disposition"]],
        disposition_label=DISPOSITION_LABEL[plan["disposition"]],
        plan_disposition=plan["disposition"],
        disposition_reason=plan["reason"],
        reviewed_by=(plan.get("reviewed_by") or "reviewer"),
        reviewed_at=(plan.get("reviewed_at") or "—"),
        wire_images=[f"wire_{v}.png" for v in ("front", "top", "right")
                     if (images / f"wire_{v}.png").is_file()],
        has_compare=(images / "compare.jpg").is_file(),
        conclusion=conclusion, detail_title=detail_title,
        detail_caption=detail_caption, engine_note=_engine_note(dest, n_pieces),
        object=_object_label(dest),
        review_rows=review_rows, interface_count=interface_count)
    (dest / f"{key}.html").write_text(html, encoding="utf-8", newline="\n")
    write_reviews(dest / "review.json", {key: {
        "key": key, "disposition": plan["disposition"], "reason": plan["reason"],
        "reviewed_by": plan.get("reviewed_by", "reviewer"),
        "reviewed_at": plan.get("reviewed_at", "")}})


# 批次显示名：按模型键自动判定（键形如 p01@smart-topology），**不写死 T2**——
# 否则 `deliver --batch standard` 生成的交付页头会说谎；审核台裁决后重渲染队列页
# 时同样走这里，故不需要额外传参。
_PRESET_LABEL = {"smart-topology": "T2", "standard": "standard 对照批"}


# 类别 slug → 中文（页面读者不该去猜 furniture 是什么）
CATEGORY_ZH = {"furniture": "家具", "tableware": "餐具", "appliance": "家电", "bag": "包袋",
               "footwear": "鞋靴", "vehicle": "车辆", "statue": "雕像", "plant": "植物",
               "instrument": "乐器", "electronics": "电子", "creature": "生物",
               "street": "街道设施", "outdoor": "户外", "float-inducing": "悬浮诱导"}


def _object_label(dest: Path) -> dict:
    """该模型"是什么"——键名（p01@smart-topology）只说明 pid 与档位，读者无从判断对象。

    取交付目录里的 `meta.json`（生成时由 Meshy 任务台账写出，随交付一起走）；
    缺失时回退到仓库根的 `prompts.jsonl`（按 pid 查）。两者都缺则返回空表，
    页面只显示键名，不写"未知"之类的噪声。
    """
    meta = dest / "meta.json"
    rec: dict = {}
    if meta.is_file():
        try:
            rec = json.loads(meta.read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, OSError):
            rec = {}
    if not rec:
        prompts = ROOT / "prompts.jsonl"
        pid = dest.name.split("@", 1)[0]
        if prompts.is_file():
            for row in read_jsonl(prompts):
                if row.get("pid") == pid:
                    rec = row
                    break
    if not rec:
        return {}
    cat = str(rec.get("category") or "")
    return {"object": str(rec.get("prompt") or ""),
            "category": CATEGORY_ZH.get(cat, cat),
            "float_inducing": str(rec.get("expect_floater")).lower() == "true"}


def _batch_label(keys: list[str]) -> str:
    presets = sorted({k.rsplit("@", 1)[-1] for k in keys if "@" in k})
    if not presets:
        return ""
    if len(presets) == 1:
        return _PRESET_LABEL.get(presets[0], presets[0])
    return " + ".join(_PRESET_LABEL.get(p, p) for p in presets)


def _subtitle(date_str: str, keys: list[str]) -> str:
    """页头副标题：日期 · 批次 · 模型数（批次缺省时不占位）。"""
    bits = [date_str]
    label = _batch_label(keys)
    if label:
        bits.append(label)
    bits.append(f"{len(keys)} 个模型")
    return " · ".join(bits)


def _composition(rows: list[dict], key: str) -> str:
    """该模型待复核检出的构成（队列卡片副标题）——比"待复核 N 条"多一层信息。"""
    sub = [r for r in rows if r["key"] == key and r["tier"] == "review"]
    if not sub:
        return "无待复核检出"
    counts = count_for_display(sub)
    bits = []
    if counts[CLASS_FLOATING]:
        bits.append(f"悬浮 {counts[CLASS_FLOATING]} 条")
    if counts[CLASS_CO_LOCATED]:
        bits.append(f"组件重叠 {counts[CLASS_CO_LOCATED]} 条")
    if counts[CLASS_OTHER]:
        bits.append(f"疑似碎屑 {counts[CLASS_OTHER]} 条")
    return " · ".join(bits)


def render_index(out_root: Path, date_str: str, keys: list[str],
                 plan: dict, rows: list[dict]) -> None:
    """渲染队列页（不碰图片；缩略图按已存在文件引用）。

    首屏信息层级（2026-09-22 审阅反馈）：先讲"多少个模型、待人工做什么"，
    再用一句白话讲存在什么问题；检出条数不是审阅者第一眼需要的信息，撤下。
    独立成函数供审核台在裁决后只重渲染队列（亚秒级）。
    """
    queues: dict[str, list[dict]] = {d: [] for d in
                                     (DISPOSITION_PASS, DISPOSITION_FAIL,
                                      DISPOSITION_PENDING)}
    for key in keys:
        p = plan[key]
        link_dir = DISPOSITION_DIR[p["disposition"]]
        queues[p["disposition"]].append({
            "key": key, "review_count": p["review_count"],
            "reason": p["reason"], "disposition": p["disposition"],
            # 卡片副标题：用"问题构成"取代与徽章重复的"待复核检出 N 条"；
            # 人工裁决写了理由就优先显示它（那是读者最需要的信息），没写则回退到问题构成
            # ——裁决理由可为空，直接取 reason 会让副标题变空行（2026-09-23 实测暴露）
            "composition": ((p["reason"] or _composition(rows, key))
                            if p["source"] == "human"
                            else _composition(rows, key)),
            "link": f"{link_dir}/{key}/",
            "object": _object_label(out_root / link_dir / key),
            "base": (out_root / link_dir / key / "images" / "base.jpg").is_file(),
            "interface_count": sum(1 for r in rows if r["key"] == key
                                   and r["defect_class"] == CLASS_CO_LOCATED
                                   and r["tier"] == "keep"),
        })
    counts = {DISPOSITION_PENDING: len(queues[DISPOSITION_PENDING]),
              DISPOSITION_PASS: len(queues[DISPOSITION_PASS]),
              DISPOSITION_FAIL: len(queues[DISPOSITION_FAIL])}
    # 白话问题摘要：只覆盖待裁决模型，按"多少个模型存在什么问题"措辞
    pending_keys = {k for k in keys
                    if plan[k]["disposition"] == DISPOSITION_PENDING}
    models_with: dict[str, set] = {}
    for r in rows:
        if r["tier"] != "review" or r["key"] not in pending_keys:
            continue
        cls = ("float" if r["defect_class"] == CLASS_FLOATING else
               "overlap" if r["defect_class"] == CLASS_CO_LOCATED else "other")
        models_with.setdefault(cls, set()).add(r["key"])
    parts = []
    if models_with.get("overlap"):
        parts.append(f"{len(models_with['overlap'])} 个有<b>组件重叠</b>"
                     "（两套面叠在同一位置，从不同角度看会来回闪烁）")
    if models_with.get("float"):
        parts.append(f"{len(models_with['float'])} 个有<b>悬浮组件</b>"
                     "（整块脱离主体、悬在空白处）")
    if models_with.get("other"):
        parts.append(f"{len(models_with['other'])} 个有<b>疑似碎屑</b>"
                     "（紧贴主体、与设计件难以区分，低优先）")
    problem_summary = (f"待人工裁决的 {counts[DISPOSITION_PENDING]} 个模型里："
                       + "；".join(parts)
                       + "（同一模型可能同时命中多项，故各项之和大于模型数）。") if parts \
        else "当前没有待人工裁决的模型。"
    iface_models = len({r["key"] for r in rows
                        if r["tier"] == "keep"
                        and r["defect_class"] == CLASS_CO_LOCATED})
    interface_note = (f"另有 {iface_models} 个模型检测到部件贴合界面——多组件拆分时"
                      "对接处的顶点被两件共用，属拆件导出的正常形态（导出特性），"
                      "<b>不是缺陷</b>，不计入复核队列。") if iface_models else ""
    index = Environment().from_string(INDEX_PAGE).render(
        css=PAGE_CSS, date=date_str, total=len(keys), counts=counts,
        subtitle=_subtitle(date_str, keys),
        has_notes=(out_root / "交付说明.md").is_file(),
        problem_summary=problem_summary, interface_note=interface_note,
        order=(DISPOSITION_PENDING, DISPOSITION_PASS, DISPOSITION_FAIL),
        labels=DISPOSITION_LABEL, queues=queues)
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "index.html").write_text(index, encoding="utf-8", newline="\n")


def deliver_one(key: str, src: Path, dest: Path, rows: list[dict],
                plan: dict, report_mod, dry: bool,
                force_transcode: bool = False) -> dict:
    """单模型：copy + 转码 + 模型页。返回该模型的展示摘要（供 index 队列）。

    队列页只用 disposition / review_count / interface_count 三个量，故此处不算缺陷类计数
    （页面盒子与结论句在 render_model_page 里按展示口径各算一次）。
    """
    review_rows, interface_count = _model_view(rows, report_mod)
    info = {"key": key, "disposition": plan["disposition"],
            "review_count": plan["review_count"], "reason": plan["reason"],
            "interface_count": interface_count, "base": False}
    if dry:
        print(f"[deliver] {key:26s} → {plan['disposition']:7s} "
              f"(待复核 {plan['review_count']}, 界面 {interface_count})")
        return info
    # glb 校验先行：坏件不建目录、不进交付与队列
    glb = src / "model.glb"
    if glb.is_file() and not glb_valid(glb):
        print(f"[deliver][warn] {key} model.glb 校验失败，跳过该模型")
        return None
    dest.mkdir(parents=True, exist_ok=True)
    # 按清单挑选拷贝（原为 shutil.copytree 全量）：render/ 原始中间件不进交付
    for name in DELIVER_FILES:
        if (src / name).is_file():
            shutil.copy2(src / name, dest / name)
    for name in DELIVER_DIRS:
        if (src / name).is_dir():
            shutil.copytree(src / name, dest / name, dirs_exist_ok=True)
    # 转码
    images = dest / "images"
    images.mkdir(exist_ok=True)
    for src_name, out_name, fmt, width in TRANSCODE:
        _transcode(src / "render" / src_name, images / out_name, fmt, width,
                   force=force_transcode)
    info["base"] = (images / "base.jpg").is_file()
    # 定位图：双机位横拼 + 标记环（源自 locator_marks.json 的投影像框）
    marks_path = src / "render" / "locator_marks.json"
    marks = {}
    if marks_path.is_file():
        try:
            marks = json.loads(marks_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            marks = {}
    for r in review_rows:
        dest_img = images / f"locator_{r['rank']}.jpg"
        src_newest = max((p.stat().st_mtime for p in
                          (src / "render").glob(f"locator_{r['rank']}_*.png")), default=0)
        if not force_transcode and dest_img.is_file() and dest_img.stat().st_mtime >= src_newest:
            continue
        canvas = _compose_locator(src / "render", r["rank"], marks)
        if canvas is not None:
            canvas.save(dest_img, format="JPEG", quality=86, optimize=True)
    # 逐检出「单件外观」列：Blender 单件隔离渲染（组件独立渲染图）
    # （piece_<rank>_{face,edge,third}.png 三视图横拼）。
    # 视图名按 PIECE_VIEWS 精确取——用通配会把旧命名残留一起卷进拼图（曾变成 6 张）。
    # 旧的 crops/（主场景高亮裁剪）已随 highlight_regions 链撤销（PLAN-07 §五）。
    for r in review_rows:
        dest_img = images / f"crop_{r['rank']}.jpg"
        views = [v for v in (src / "render" / f"piece_{r['rank']}_{name}.png"
                             for name in PIECE_VIEWS) if v.is_file()]
        # 增量：合成图已存在且不旧于任一源视图则跳过（_hstrip 是交付层最重的一步，
        # 256 条检出 × 3 视图的 PNG 解码；只改文案/页面结构时不应重做）
        newest = max((v.stat().st_mtime for v in views), default=0)
        if views and (force_transcode or not dest_img.is_file()
                      or dest_img.stat().st_mtime < newest):
            strip = _hstrip([Image.open(v).convert("RGB") for v in views])
            dest_img.parent.mkdir(parents=True, exist_ok=True)
            strip.save(dest_img, format="JPEG", quality=88, optimize=True)
    # images/ 白名单清理：撤下某个图种后，旧交付里同名文件不会自己消失
    # （与"旧命名单件残留被通配卷进拼图"同类）。页面只引用下面这些，其余一律删。
    keep = {out for _, out, _, _ in TRANSCODE}
    keep |= {f"crop_{r['rank']}.jpg" for r in review_rows}
    keep |= {f"locator_{r['rank']}.jpg" for r in review_rows}
    for f in sorted(images.iterdir()):
        if f.is_file() and f.name not in keep:
            f.unlink()
            print(f"[deliver] {key} 清理已撤下的图：{f.name}")
    # 模型页 + 裁决镜像（复用独立渲染函数，审核台共用）
    render_model_page(key, dest, rows, plan, report_mod)
    return info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成交付目录 results/<日期>（PLAN-05）")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--batch", choices=("smart-topology", "standard", "all"),
                    default="smart-topology",
                    help="交付/审查范围：默认仅 T2（smart-topology）。standard 不进"
                         "审查队列——其对照叙事由冻结批报告承担")
    ap.add_argument("--source", type=Path, default=DATA_DIR)
    ap.add_argument("--out", type=Path, default=RESULTS_ROOT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--snapshot", action="store_true",
                    help="发布快照模式：剔除逐件 GLB（只留 pieces/manifest.json）"
                         "并把交付说明拷进根目录——产物即可直接 commit 的离线只读网页")
    args = ap.parse_args(argv)

    findings_path = args.source / "findings.jsonl"
    rows = read_findings(findings_path) if findings_path.is_file() else []
    raw = args.source / "raw"
    suffix = _BATCH_SUFFIX = {"smart-topology": "@smart-topology",
                              "standard": "@standard"}.get(args.batch)
    keys = sorted(d.name for d in raw.iterdir()
                  if d.is_dir() and (d / "model.glb").is_file()
                  and (suffix is None or d.name.endswith(suffix)))
    if not keys:
        print(f"无可交付模型（raw={raw}，batch={args.batch}），未生成 results")
        return 0
    reviews = load_reviews(args.source / "review.jsonl")
    plan = plan_dispositions(rows, reviews, keys)
    report_mod = _load_report_mod()

    out_root = args.out / args.date
    print(f"[deliver] results/{args.date}/（{len(keys)} 模型，batch={args.batch}）")
    # 类别目录清理（幂等 + 范围互斥）：**只删该删的**——范围外的批次目录（如 standard
    # 不进 T2 队列）与落位已变的旧类别（改判残留），未变化者原地保留。
    # 为什么不能像早期那样"清空全部再重建"：全量 rmtree 会让下游所有 mtime 增量跳过
    # （转码、逐检出合成图）失效——每次重跑都重做 256 张合成图，只改一句文案也要 30 秒。
    if not args.dry_run:
        for disp, dirname in DISPOSITION_DIR.items():
            dd = out_root / dirname
            if not dd.is_dir():
                continue
            for child in dd.iterdir():
                if not child.is_dir():
                    continue
                if child.name not in keys:
                    shutil.rmtree(child)                      # 范围外批次
                elif DISPOSITION_DIR[plan[child.name]["disposition"]] != dirname:
                    shutil.rmtree(child)                      # 改判残留
    queues: dict[str, list[dict]] = {d: [] for d in
                                     (DISPOSITION_PASS, DISPOSITION_FAIL,
                                      DISPOSITION_PENDING)}
    # 转码规格指纹：与上次不同则忽略 mtime 跳过，全量重转（防旧规格产物留存）
    spec_path = out_root / ".transcode_spec.json"
    spec_now = _spec_signature()
    force_transcode = True
    if spec_path.is_file():
        try:
            force_transcode = (json.loads(spec_path.read_text(encoding="utf-8"))
                               .get("sig") != spec_now)
        except json.JSONDecodeError:
            force_transcode = True
    if force_transcode and not args.dry_run:
        print("[deliver] 转码规格有变 → 本次忽略增量跳过，全量重转")
    delivered: list[str] = []
    for key in keys:
        disp = plan[key]["disposition"]
        info = deliver_one(key, raw / key,
                           out_root / DISPOSITION_DIR[disp] / key,
                           [r for r in rows if r["key"] == key],
                           {**plan[key], "reviewed_by": reviews.get(key, {}).get(
                               "reviewed_by", "reviewer"),
                               "reviewed_at": reviews.get(key, {}).get(
                               "reviewed_at", "")},
                           report_mod, args.dry_run,
                           force_transcode=force_transcode)
        if info is None:      # glb 校验失败的跳过件：不进队列
            continue
        delivered.append(key)
        info["link"] = f"{DISPOSITION_DIR[disp]}/{key}/"
        queues[disp].append(info)

    if args.dry_run:
        print("[deliver] dry-run：未写任何文件")
        return 0

    # 队列页（复用独立渲染函数）+ 附录
    all_rows = [r for r in rows if r["key"] in delivered]
    render_index(out_root, args.date, delivered, plan, all_rows)
    out_data = out_root / "data"
    out_data.mkdir(exist_ok=True)
    # 附录按**本次交付的批次**裁剪（batch=None 时整库）：JSONL 逐记录过滤，按批次分文件的
    # 汇总类文件二选一。理由与数据包一致——交付讲 T2，附录里混着 standard 的记录会让读者
    # 对不上（那些键在交付的模型里根本没有），也破坏"从数据包重建出来的快照与仓库里一致"。
    for name in ("findings.jsonl", "islands.jsonl", "summary_t2.json",
                 "summary-standard-v2.json"):
        src_file = args.source / name
        if not src_file.is_file():
            continue
        if name in BATCH_SUMMARY_FILES:
            if BATCH_SUMMARY_FILES[name] == args.batch:
                shutil.copy(src_file, out_data / name)
            elif (out_data / name).is_file():
                # 不该带的那份：**删掉旧副本**而不是"这次不拷"——否则它留在附录里继续说谎
                # （与 memory 里"产物集一变旧产物不会自己消失"同族）
                (out_data / name).unlink()
                print(f"[deliver] 附录清理：移除不属于本批次的 {name}")
            continue
        recs = [r for r in read_jsonl(src_file) if in_batch(r.get("key", ""), args.batch)]
        nl = chr(10)
        (out_data / name).write_text(
            nl.join(json.dumps(r, ensure_ascii=False) for r in recs) + nl, encoding="utf-8", newline="\n")
    # prompt 定义随交付走（任务书要求交代"prompt 是怎么选的"，此前只在仓库根）
    prompts = ROOT / "prompts.jsonl"
    if prompts.is_file():
        shutil.copy(prompts, out_data / "prompts.jsonl")
    # 复核档案镜像：交付根的 review.jsonl 是状态源（data/review.jsonl）的只读投影。
    # **状态源为空时必须删掉旧镜像**——否则清空裁决后重建，交付里会留一份过期的复核档案
    # 与页面（全是"未复核"）自相矛盾（2026-09-23 复位实测踩到）。
    mirror = out_root / "review.jsonl"
    if reviews:
        write_reviews(mirror, reviews)
    elif mirror.is_file():
        mirror.unlink()
        print("[deliver] 状态源无裁决记录 → 删除交付目录里的过期复核档案 review.jsonl")
    # 交付说明（交付项之一）：**以交付目录里的副本为准，重建不清除它**。
    # 若仓库里放了带日期的源文件（`deliverable/<日期>-*.md`）则用它刷新副本；没有源文件
    # 时只提示一次、不报错——本仓库不再跟踪这份文档（它单独维护与提交），交付快照里的
    # 那份就是唯一在维护的版本（2026-09-23 用户口径）。
    #
    # 旧名迁移：这份文档此前叫 `一页说明.md`（2026-09-23 统一改为"交付说明"）。老交付目录里
    # 的旧名副本**就地改名**而不是各留一份——同名不同义的残留是这套管线反复踩的坑
    # （见 `.claude/memory/2026-09-23-composite-mtime-skip-stale-crops.md`）。
    legacy = out_root / "一页说明.md"
    if legacy.is_file() and not (out_root / "交付说明.md").is_file():
        legacy.rename(out_root / "交付说明.md")
        print("[deliver] 交付说明：旧名 一页说明.md 已就地改名为 交付说明.md")
    notes = sorted((ROOT / "deliverable").glob(f"{args.date}-*.md"))
    if notes:
        shutil.copy(notes[0], out_root / "交付说明.md")
    elif not (out_root / "交付说明.md").is_file():
        print("[deliver] 未提供 交付说明 源文件（deliverable/<日期>-*.md），"
              "交付目录里也没有既有副本 → 本次交付不含交付说明")
    if args.snapshot:
        # 逐件 GLB 是取证用的中间件（页面暂未链接），快照只留 manifest.json
        # ——页面结论句的组件数就取自它，必须留
        gone = 0
        for f in out_root.glob("*/*/pieces/*.glb"):
            f.unlink()
            gone += 1
        print(f"[deliver] 快照模式：剔除逐件 GLB {gone} 个（保留 pieces/manifest.json）")
    spec_path.write_text(json.dumps({"sig": spec_now, "spec": TRANSCODE},
                                    ensure_ascii=False, indent=1),
                         encoding="utf-8", newline="\n")
    print(f"[deliver] 完成 → {out_root}（index.html + {len(keys)} 模型页）")
    return 0


def _count(rows: list[dict], cls: str) -> dict:
    sub = [r for r in rows if r["defect_class"] == cls]
    return {"findings": len(sub), "models": len({r["key"] for r in sub})}


def _count_review(rows: list[dict], cls: str) -> dict:
    sub = [r for r in rows if r["defect_class"] == cls and r["tier"] == "review"]
    return {"findings": len(sub), "models": len({r["key"] for r in sub})}


if __name__ == "__main__":
    sys.exit(main())
