import json
from pathlib import Path

from PIL import Image

import meshq.stages.report as rep
ROOT = Path(__file__).resolve().parent.parent


def make_png(path: Path, size=(640, 480), color=(128, 128, 128)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


def make_fake_data(tmp_path: Path, fixed: bool):
    raw = tmp_path / "raw" / "pX@standard"
    (raw / "render").mkdir(parents=True)
    make_png(raw / "render" / "base_iso.png")
    for v in ("front", "top", "right"):
        make_png(raw / "render" / f"base_{v}.png")
        make_png(raw / "render" / f"highlight_{v}.png")
        make_png(raw / "render" / f"wire_{v}.png")
    make_png(raw / "render" / "compare_front.png")
    (raw / "meta.json").write_text(json.dumps(
        {"key": "pX@standard", "category": "furniture"}), encoding="utf-8")
    (raw / "blender_checks.json").write_text(json.dumps(
        {"n_pieces": 1, "non_manifold_edges": 0, "blender_version": "5.0.1"}),
        encoding="utf-8")
    (tmp_path / "metrics.jsonl").write_text(json.dumps(
        {"key": "pX@standard", "n_faces": 500, "n_pieces": 1,
         "non_manifold_edges": 0, "watertight": True}) + "\n", encoding="utf-8")
    (tmp_path / "flags.jsonl").write_text(json.dumps(
        {"key": "pX@standard", "verdict": "直接入库", "n_pieces": 1,
         "n_small_pieces": 0, "fragment_face_ratio": 0.0,
         "non_manifold_edges": 0}) + "\n", encoding="utf-8")
    (tmp_path / "tasks.json").write_text(json.dumps(
        [{"key": "pX@standard", "prompt": "a chair", "credits": 20}]),
        encoding="utf-8")

    if fixed:
        fr = tmp_path / "fixed" / "pX@standard" / "render"
        fr.mkdir(parents=True)
        for v in ("front", "top", "right"):
            make_png(fr / f"base_{v}.png", color=(200, 200, 200))
            make_png(fr / f"wire_{v}.png", color=(60, 60, 60))
        make_png(fr / "compare_front.png", size=(1288, 480))
        (tmp_path / "fixed" / "pX@standard" / "before_after.json").write_text(
            json.dumps({"before_faces": 6, "after_faces": 3,
                        "removed_faces": 3, "before_pieces": 2,
                        "after_pieces": 1}), encoding="utf-8")


def make_cfg():
    return {"detect": {"diag_ratio": 0.1, "vol_ratio": 0.01,
                       "frag_reject_ratio": 0.05}}


def test_direct_card_has_trio_and_highlight(tmp_path, monkeypatch):
    make_fake_data(tmp_path, fixed=False)
    monkeypatch.setattr(rep, "DATA_DIR", tmp_path)
    monkeypatch.setattr(rep, "load_config", make_cfg)

    s = rep.build_summary(tmp_path)
    assert s["funnel"] == {"total": 1, "direct": 1, "fix": 0, "reject": 0}
    c = s["cards"][0]
    assert c["fixed"] is False
    assert len(c["before_base"]) == 3 and all(c["before_base"])
    assert len(c["before_hl"]) == 3 and all(c["before_hl"])
    assert len(c["after_wire"]) == 3 and all(c["after_wire"])
    assert c["compare"] != ""
    # 内嵌图已降采样到 420 宽（base64 解码后验证）
    import base64, io
    img = Image.open(io.BytesIO(base64.b64decode(c["before_base"][0])))
    assert img.width <= 420


def test_fixed_card_shows_before_and_after(tmp_path, monkeypatch):
    make_fake_data(tmp_path, fixed=True)
    monkeypatch.setattr(rep, "DATA_DIR", tmp_path)
    monkeypatch.setattr(rep, "load_config", make_cfg)

    s = rep.build_summary(tmp_path)
    c = s["cards"][0]
    assert c["fixed"] is True
    assert len(c["before_base"]) == len(c["before_hl"]) == 3
    assert len(c["after_base"]) == len(c["after_wire"]) == 3
    assert c["fix_diff"]["removed_faces"] == 3
    slim_keys = {k for k in c if k in ("thumb", "before_base", "compare")}
    assert slim_keys  # 卡片带图字段存在（summary.json 落盘前再剥离）


def test_main_writes_html_and_slim_summary(tmp_path, monkeypatch):
    make_fake_data(tmp_path, fixed=True)
    monkeypatch.setattr(rep, "DATA_DIR", tmp_path)
    monkeypatch.setattr(rep, "REPORT_HTML", tmp_path / "report.html")
    monkeypatch.setattr(rep, "SUMMARY_JSON", tmp_path / "summary.json")
    monkeypatch.setattr(rep, "load_config", make_cfg)

    assert rep.main([]) == 0
    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "pX@standard" in html and "直接入库" in html
    assert "判别结果着色 + 线框" in html and "网格线框三视图" in html  # 两组三视图
    assert "形态定妆" not in html  # 平滑三视图已从卡片撤下
    assert "待审组件逐个放大" not in html  # 无 A/C 档组件的卡片不出现拼图区
    s = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert s["funnel"]["total"] == 1
    assert "thumb" not in s["cards"][0] and "before_base" not in s["cards"][0]
    assert "collage" not in s  # 拼贴总览已删（420px 内嵌必糊）


# ---------------------------------------------------------------- T2 四档报告（D6 接线）

def make_decision(tier, rank, faces, entry, reason,
                  diag_ratio=0.2, gap_ratio=0.01):
    return {"tier": tier, "entry": entry, "reason": reason, "rank": rank,
            "n_faces": faces, "diag_ratio": diag_ratio, "gap_ratio": gap_ratio}


def test_disposition_of_four_tier_mapping():
    """T2 漏斗判别器驱动（D6 修订）：legacy 拒绝不再占用档位，退为卡片注记。"""
    fix_flag = {"verdict": "修复后入库"}
    direct_flag = {"verdict": "直接入库"}
    reject_flag = {"verdict": "拒绝"}
    counts_ac = {"A": 0, "B": 1, "C": 3}
    # 任一 C 档件 → 整模型待审（legacy 拒绝/修复口径不再抢先）
    assert rep.disposition_of(reject_flag, {"counts": counts_ac}) == "待审"
    assert rep.disposition_of(direct_flag, {"counts": counts_ac}) == "待审"
    # 有 A 档件 → 修复后入库
    assert rep.disposition_of(direct_flag, {"counts": {"A": 2, "B": 1, "C": 0}}) == "修复后入库"
    # 全部件/本体 → 直接入库（legacy 修复口径不再抬档）
    assert rep.disposition_of(fix_flag, {"counts": {"A": 0, "B": 2, "C": 0}}) == "直接入库"
    # 无判别表 → 退回旧三档，不编造结论
    assert rep.disposition_of(direct_flag, None) == "直接入库"
    assert rep.disposition_of(fix_flag, None) == "修复后入库"
    assert rep.disposition_of(reject_flag, None) == "拒绝"


def make_t2_data(tmp_path: Path):
    """standard 键位一枚（不应进 T2 报告）+ T2 键位一枚（含 2 个 C 档件）。"""
    make_fake_data(tmp_path, fixed=False)
    key = "pY@smart-topology"
    raw = tmp_path / "raw" / key
    (raw / "render").mkdir(parents=True)
    make_png(raw / "render" / "base_iso.png", color=(10, 10, 60))
    for v in ("front", "top", "right"):
        make_png(raw / "render" / f"base_{v}.png")
        make_png(raw / "render" / f"highlight_{v}.png")
        make_png(raw / "render" / f"wire_{v}.png")
    make_png(raw / "render" / "compare_front.png")
    (raw / "meta.json").write_text(json.dumps(
        {"key": key, "category": "furniture"}), encoding="utf-8")
    (raw / "blender_checks.json").write_text(json.dumps(
        {"n_pieces": 4, "non_manifold_edges": 0, "blender_version": "5.0.1"}),
        encoding="utf-8")
    for path, rec in (
        (tmp_path / "metrics.jsonl", {"key": key, "n_faces": 9000, "n_pieces": 4,
                                      "non_manifold_edges": 0, "watertight": False}),
        (tmp_path / "flags.jsonl", {"key": key, "verdict": "修复后入库", "n_pieces": 4,
                                    "n_small_pieces": 1, "fragment_face_ratio": 0.01,
                                    "non_manifold_edges": 0}),
    ):
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    tasks = json.loads((tmp_path / "tasks.json").read_text(encoding="utf-8"))
    tasks.append({"key": key, "prompt": "a low-poly chair", "credits": 5})
    (tmp_path / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
    pv = {"key": key, "source": "metrics", "n_pieces": 4, "bpy_n_pieces": 4,
          "engine_partition_match": True,
          "counts": {"A": 0, "B": 2, "C": 2},
          "auto_fix_ranks": [], "review_ranks": [3, 4],
          "decisions": [
              make_decision("B", 0, 8000, "main", "主组件（bbox 对角线最大，faces=8000）"),
              make_decision("B", 1, 700, "B", "贴合、同尺寸同类 2 件；faces=700"),
              make_decision("C", 3, 2, "A0",
                            "真实性门：仅 trimesh 侧存在；faces=2、对角线比 0.001"),
              make_decision("C", 4, 6, "A1-off",
                            "形态极低（faces=6、非封闭）但该入口尚未取证，降级待审"),
          ]}
    (tmp_path / "part_verdicts.jsonl").write_text(
        json.dumps(pv, ensure_ascii=False) + "\n", encoding="utf-8")


def test_t2_summary_four_tier_and_batch_filter(tmp_path, monkeypatch):
    make_t2_data(tmp_path)
    monkeypatch.setattr(rep, "load_config", make_cfg)

    s = rep.build_summary(tmp_path, batch="smart-topology")
    assert s["t2"] is True
    # 批次过滤：standard 键位不进 T2 报告（D9 两档不合并）
    assert [c["key"] for c in s["cards"]] == ["pY@smart-topology"]
    # 四档漏斗：含 C 档件 → 待审
    assert s["funnel"] == {"total": 1, "direct": 0, "fix": 0, "review": 1, "reject": 0}
    c = s["cards"][0]
    assert c["verdict"] == "待审" and c["verdict_cls"] == "review"
    assert c["part_counts"] == {"A": 0, "B": 2, "C": 2}
    # 判别分区：A 档零样本如实呈现；C 档复核表逐条带理由
    assert s["parts"]["a_rows"] == []
    g = s["parts"]["c_groups"]
    assert len(g) == 1 and g[0]["key"] == "pY@smart-topology"
    # 复核顺序：入口叙事序（A0 → A1-off），同组按面数降序
    assert [(r["entry"], r["rank"]) for r in g[0]["rows"]] == [("A0", 3), ("A1-off", 4)]
    assert all(r["reason"] for r in g[0]["rows"])


def test_t2_main_writes_report_t2_html(tmp_path, monkeypatch):
    make_t2_data(tmp_path)
    monkeypatch.setattr(rep, "DATA_DIR", tmp_path)
    monkeypatch.setattr(rep, "REPORT_HTML", tmp_path / "report.html")
    monkeypatch.setattr(rep, "SUMMARY_JSON", tmp_path / "summary.json")
    monkeypatch.setattr(rep, "load_config", make_cfg)

    assert rep.main(["--batch", "smart-topology"]) == 0
    html = (tmp_path / "report_t2.html").read_text(encoding="utf-8")
    assert "部件判别分区" in html and "待审复核表" in html
    assert "自动删除清单" in html
    assert "零样本" in html  # A 档为空的诚实结论
    assert "字段与规则对照" in html  # 复核表名词表
    assert "建议复核路径" in html  # 复核策略前置
    assert "pY@smart-topology" in html
    assert "pX@standard" not in html  # standard 键位被批次过滤
    assert "待审 1" in html  # 漏斗四档盒子
    s = json.loads((tmp_path / "summary_t2.json").read_text(encoding="utf-8"))
    assert s["funnel"]["review"] == 1
    assert s["cards"][0]["part_counts"] == {"A": 0, "B": 2, "C": 2}
    assert "thumb" not in s["cards"][0]
    # 复核表的图片字段已剥离，理由串保留
    assert s["parts"]["c_groups"][0]["rows"][0]["reason"]


# ---------------------------------------------------------------- v2 检出版式（PLAN-04）

def make_findings_rows(keys_ranks):
    """findings.make_finding 产出合法检出行。

    keys_ranks: [(key, rank, class)] 或 [(key, rank, class, subtype)]；
    共位行的 tier 由 subtype 决定（interface → keep，其余 review）。
    """
    from meshq.core.findings import make_finding
    rows = []
    for item in keys_ranks:
        key, rank, cls = item[:3]
        subtype = item[3] if len(item) > 3 else None
        if cls == "co_located":
            tier = "keep" if subtype == "interface" else "review"
            evidence = {"bbox_pinned": True, "engine_seen_by_bpy": False,
                        "co_located_subtype": subtype or "overlap",
                        "neighbor_rank": 0}
            metrics = {"gap_ratio": 0.05, "diag_ratio": 0.01,
                       "overlap_frac": 0.9 if subtype == "overlap"
                       else (0.3 if subtype == "partial" else 0.05)}
            detector, entry = "co_located", "A0"
        else:
            tier, evidence, metrics = "review", {"island_id": 1,
                                                 "island_ranks": [rank]}, {}
            detector, entry = "islands", "island"
        rows.append(make_finding(
            key=key, detector=detector, defect_class=cls, rank=rank, tier=tier,
            entry=entry, n_faces=6, reason=f"测试理由串 {key}#{rank}",
            evidence=evidence, metrics=metrics))
    return rows


def make_t2_findings(tmp_path: Path):
    """给 make_t2_data 的批次补 findings.jsonl：重叠共位 + 灰区 + 悬浮 + 非核心
    + 界面贴合（keep，转导出特性度量）。"""
    key = "pY@smart-topology"
    rows = make_findings_rows([
        (key, 3, "co_located", "overlap"),
        (key, 6, "co_located", "partial"),
        (key, 7, "co_located", "interface"),
        (key, 4, "floating"),
        (key, 5, "other"),
    ])
    (tmp_path / "findings.jsonl").write_text(
        "\n".join(__import__("json").dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")


def test_v2_summary_detection_layout(tmp_path, monkeypatch):
    make_t2_data(tmp_path)
    make_t2_findings(tmp_path)
    monkeypatch.setattr(rep, "load_config", make_cfg)

    s = rep.build_summary(tmp_path, batch="smart-topology")
    det = s["detections"]
    # 共位 review 计数只含重叠型+灰区；界面贴合独立为导出特性度量
    assert det["summary"]["co_located"]["findings"] == 2       # overlap + partial
    assert det["summary"]["interface"] == {"findings": 1, "models": 1}
    assert [c for c in det["class_order"]] == ["floating", "co_located", "other"]
    assert det["groups"]["co_located"]["total"] == 2           # keep 行不进复核表
    g = det["groups"]["co_located"]["models"][0]["rows"]
    assert [r["evidence"]["co_located_subtype"] for r in g] == ["overlap", "partial"]
    assert "处于[20%, 60%]，需要人工确认正确性" in g[1]["evidence_str"]   # 灰区判据的定稿措辞
    assert "非核心" in det["noncore_note"]
    # 卡片：v2 结论句 + 待复核徽章 + 检出计数（含界面贴合单列）
    c = s["cards"][0]
    assert c["verdict"] == "待复核"
    assert c["detect_counts"] == {"floating": 1, "co_located": 2,
                                  "other": 1, "interface": 1}
    assert "本模型共 4 个组件" in c["conclusion"]        # 组件总数单独成句，不与"检出数"混淆
    assert "待人工复核 4 条" in c["conclusion"]          # 三类之和 = review 总数（口径一致）
    assert "悬浮 1 条、组件重叠 2 条、疑似碎屑 1 条（低优先）" in c["conclusion"]
    assert "其中：悬浮是整块脱离主体" in c["conclusion"]  # 只解释实际存在的问题类别
    # 结论句要一并解释"部件贴合界面"是什么（不只是给数字）
    assert "部件贴合界面 1 处——多组件拆分时对接处的顶点被两件共用" in c["conclusion"]


def test_v2_html_has_no_auto_delete_wording(tmp_path, monkeypatch):
    make_t2_data(tmp_path)
    make_t2_findings(tmp_path)
    monkeypatch.setattr(rep, "DATA_DIR", tmp_path)
    monkeypatch.setattr(rep, "REPORT_HTML", tmp_path / "report.html")
    monkeypatch.setattr(rep, "SUMMARY_JSON", tmp_path / "summary.json")
    monkeypatch.setattr(rep, "load_config", make_cfg)

    assert rep.main(["--batch", "smart-topology"]) == 0
    html = (tmp_path / "report_t2.html").read_text(encoding="utf-8")
    assert "检出汇总" in html and "按缺陷类分组" in html
    assert "悬浮碎片" in html and "重叠面" in html
    assert "贴合界面" in html and "导出特性" in html   # 界面贴合 = 度量盒
    assert "方法学节" in html                    # legacy 漏斗移入方法学节
    assert "自动删除清单" not in html            # v2 无 A 档自动删区
    assert "自动删除候选" not in html            # 图注无自动删表述
    assert "待人工复核" in html
    assert "pX@standard" not in html
    s = json.loads((tmp_path / "summary_t2.json").read_text(encoding="utf-8"))
    assert s["detections"]["summary"]["floating"]["findings"] == 1
    assert s["detections"]["summary"]["interface"]["findings"] == 1
    assert s["cards"][0]["conclusion"]
    # 分组复核表图片已剥离，理由串保留
    g = s["detections"]["groups"]["co_located"]["models"][0]
    assert g["rows"][0]["reason"]


def test_legacy_layout_without_findings_unchanged(tmp_path, monkeypatch):
    """无 findings.jsonl 的批次保持 legacy 版式（冻结归档口径可复现）。"""
    make_t2_data(tmp_path)
    monkeypatch.setattr(rep, "load_config", make_cfg)
    s = rep.build_summary(tmp_path, batch="smart-topology")
    assert "detections" not in s
    assert "parts" in s and s["parts"]["c_groups"][0]["rows"][0]["entry"] == "A0"
    assert s["cards"][0]["verdict"] == "待审"
