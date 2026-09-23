"""deliver（原 06_deliver）：交付生成器的守卫（PLAN-05 §二/§三/§六）。

覆盖：三类别落位（含 fail 仅人工、界面贴合不计入）、按清单拷贝（不携带 render/ 原始
中间件）+ glb 校验、图片转码（JPG/PNG 按类型）、双层 HTML、裁决镜像、幂等重跑、
dry-run 零写入。
"""

import json
from pathlib import Path

from PIL import Image
import pytest

import meshq.stages.deliver as mod
from fakes import write_valid_glb
from meshq.core.review import (DISPOSITION_FAIL, DISPOSITION_PASS, DISPOSITION_PENDING,
                    make_record, write_reviews)

ROOT = Path(__file__).resolve().parent.parent
DATE = "2026-09-22"
NOTE_OLD = "# 旧名说明" + chr(10)      # 交付说明的旧文件名内容（换行用 chr(10) 拼，避免转义）
NOTE_NEW = "# 现名说明" + chr(10)


def png(path, size=(320, 240), color=(120, 120, 120)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


def make_model(src, key, *, valid_glb=True):
    d = src / "raw" / key
    d.mkdir(parents=True, exist_ok=True)
    if valid_glb:
        write_valid_glb(d / "model.glb")
    else:
        d.joinpath("model.glb").write_bytes(b"broken")
    r = d / "render"
    png(r / "base_iso.png")
    for v in ("front", "top", "right"):
        png(r / f"highlight_{v}.png", (1280, 960))
        png(r / f"wire_{v}.png", (1280, 960), (30, 30, 30))


def make_world(tmp_path):
    src = tmp_path / "data"
    make_model(src, "pClean@smart-topology")
    make_model(src, "pDirty@smart-topology")
    # 单引擎件的 Blender 单件隔离渲染假件（真 pipeline 由 render_one 产出）。
    # 视图名取 lookdev_math.PIECE_VIEWS（face/edge/third）；额外放一张旧命名残留，
    # 用于验证交付层按视图名精确取图、不会把它卷进条带（曾导致条带 6 张）
    from meshq.core.lookdev_math import PIECE_VIEWS
    for v in PIECE_VIEWS:
        png(src / "raw" / "pDirty@smart-topology" / "render" / f"piece_3_{v}.png",
            (300, 220), (90, 90, 90))
    png(src / "raw" / "pDirty@smart-topology" / "render" / "piece_3_right.png",
        (300, 220), (200, 30, 30))
    # 逐件 GLB（piece store 产物）——随交付走，供图例按 rank 链到
    # 定位图源件（双机位）+ 投影像框记录（交付层据此画标记环）
    lr = src / "raw" / "pDirty@smart-topology" / "render"
    png(lr / "locator_3_model.png", (1280, 960), (245, 245, 245))
    png(lr / "locator_3_close.png", (1280, 960), (250, 250, 250))
    (lr / "locator_marks.json").write_text(
        '{"3": {"model": [600.0, 400.0, 640.0, 440.0]}}', encoding="utf-8")
    pieces = src / "raw" / "pDirty@smart-topology" / "pieces"
    pieces.mkdir(parents=True, exist_ok=True)
    (pieces / "3.glb").write_bytes(b"piece3")
    (pieces / "manifest.json").write_text('{"n_pieces": 1}', encoding="utf-8")
    make_model(src, "pBroken@smart-topology", valid_glb=False)
    from meshq.core.findings import make_finding
    rows = [
        # pClean：只有界面贴合（keep）→ 无待复核检出 → 确定通过
        make_finding(key="pClean@smart-topology", detector="co_located",
                     defect_class="co_located", rank=1, tier="keep",
                     reason="界面贴合", evidence={"co_located_subtype": "interface"},
                     metrics={"overlap_frac": 0.05}),
        # pDirty：悬浮 review 1 条 + 界面贴合 keep 2 条
        make_finding(key="pDirty@smart-topology", detector="islands",
                     defect_class="floating", rank=3, reason="悬浮碎片候选"),
        make_finding(key="pDirty@smart-topology", detector="co_located",
                     defect_class="co_located", rank=1, tier="keep",
                     reason="界面贴合", evidence={"co_located_subtype": "interface"},
                     metrics={"overlap_frac": 0.05}),
        make_finding(key="pDirty@smart-topology", detector="co_located",
                     defect_class="co_located", rank=2, tier="keep",
                     reason="界面贴合", evidence={"co_located_subtype": "interface"},
                     metrics={"overlap_frac": 0.05}),
    ]
    (src / "findings.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")
    # 人工裁决：pDirty 判废（fail 仅人工产生）
    write_reviews(src / "review.jsonl",
                  {"pDirty@smart-topology": make_record(
                      "pDirty@smart-topology", DISPOSITION_FAIL,
                      reason="整组位移判废", reviewed_at="2026-09-22T10:00:00Z")})
    return src


def run_deliver(tmp_path, src, *extra, dry=False):
    argv = ["--date", DATE, "--source", str(src), "--out", str(tmp_path / "results")]
    if dry:
        argv.append("--dry-run")
    rc = mod.main(argv + list(extra))
    return rc, mod


def test_object_label_from_meta(tmp_path):
    """模型"是什么"取自随交付走的 meta.json（键名 p01@smart-topology 读者看不出对象）。"""
    d = tmp_path / "p01@smart-topology"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({
        "pid": "p01", "prompt": "a wooden dining chair",
        "category": "furniture", "expect_floater": "False"}), encoding="utf-8")

    got = mod._object_label(d)

    assert got == {"object": "a wooden dining chair", "category": "家具",
                   "float_inducing": False}


def test_object_label_falls_back_to_prompts_jsonl(tmp_path, monkeypatch):
    """meta.json 缺失（旧交付目录）时按 pid 回退查 prompts.jsonl。"""
    (tmp_path / "prompts.jsonl").write_text(json.dumps({
        "pid": "p09", "prompt": "a guitar", "category": "instrument",
        "expect_floater": False}) + "\n", encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    d = tmp_path / "p09@smart-topology"
    d.mkdir()

    got = mod._object_label(d)

    assert got["category"] == "乐器" and got["object"] == "a guitar"


def test_object_label_absent_returns_empty(tmp_path, monkeypatch):
    """两边都查不到时返回空表（页面只显示键名，不写"未知"之类的噪声）。"""
    monkeypatch.setattr(mod, "ROOT", tmp_path)     # 无 prompts.jsonl
    d = tmp_path / "pX@smart-topology"
    d.mkdir()

    assert mod._object_label(d) == {}


def test_appendix_is_scoped_to_delivered_batch(tmp_path):
    """交付附录按批次裁剪：别的批次的检出记录不该出现在本交付的数据附录里。

    此前的行为是"原样拷贝 findings.jsonl"，于是 T2 交付的附录里混着 standard 批的记录
    （那些键在交付的模型里根本没有），并且让"用 T2 数据包重建的快照"与仓库里的不一致。
    """
    src = make_world(tmp_path)
    (src / "findings.jsonl").write_text(
        '{"key": "pDirty@smart-topology", "detector": "islands", "defect_class": "floating",'
        ' "tier": "review", "rank": 3, "n_faces": 5, "reason": "r"}' + chr(10)
        + '{"key": "pOther@standard", "detector": "islands", "defect_class": "floating",'
        ' "tier": "review", "rank": 3, "n_faces": 5, "reason": "r"}' + chr(10), encoding="utf-8")
    # 别的批次的汇总文件不该被带进 T2 交付
    (src / "summary-standard-v2.json").write_text("{}", encoding="utf-8")
    (src / "summary_t2.json").write_text("{}", encoding="utf-8")

    rc, _ = run_deliver(tmp_path, src)

    assert rc == 0
    out = tmp_path / "results" / DATE / "data"
    body = (out / "findings.jsonl").read_text(encoding="utf-8")
    assert "pDirty@smart-topology" in body and "pOther@standard" not in body
    assert (out / "summary_t2.json").is_file()
    assert not (out / "summary-standard-v2.json").exists()
    # 重建第二次：上一轮留下的"别的批次的汇总"要被清掉，而不是一直留着
    rc, _ = run_deliver(tmp_path, src)
    assert rc == 0 and not (out / "summary-standard-v2.json").exists()


def test_deliver_migrates_legacy_note_name(tmp_path):
    """旧名 `一页说明.md` 就地改名为 `交付说明.md`，不留两份同名不同义的副本。

    这段迁移逻辑本身也曾是"没测过就写的分支"——第一版用了未导入的 `os.replace`，
    只因为没有夹具触发这条分支，全套测试照样绿（2026-09-23 自查发现）。
    """
    src = make_world(tmp_path)
    out = tmp_path / "results"
    legacy = out / DATE / "一页说明.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(NOTE_OLD, encoding="utf-8")

    rc, _ = run_deliver(tmp_path, src)

    assert rc == 0
    assert (out / DATE / "交付说明.md").is_file()          # 已迁移
    assert not legacy.exists()                              # 旧名不再存在
    assert (out / DATE / "交付说明.md").read_text(encoding="utf-8") == NOTE_OLD


def test_deliver_keeps_existing_new_note(tmp_path):
    """已有 `交付说明.md` 时不动它（重建不清除既有副本）。"""
    src = make_world(tmp_path)
    out = tmp_path / "results"
    note = out / DATE / "交付说明.md"
    note.parent.mkdir(parents=True)
    note.write_text(NOTE_NEW, encoding="utf-8")

    rc, _ = run_deliver(tmp_path, src)

    assert rc == 0 and note.read_text(encoding="utf-8") == NOTE_NEW


def test_transcode_missing_source_returns_false(tmp_path):
    """源缺失返回 False、**不抛异常**——即便目标已存在（曾被 src.stat() 抢先炸掉）。

    顺序 bug 的后果：交付目录里已有图 + 某个渲染源被删（例如清理 render/ 中间件），
    下一次 deliver 当场 FileNotFoundError，而不是按 docstring 的承诺优雅跳过。
    """
    src = tmp_path / "base_iso.png"
    dest = tmp_path / "base.jpg"
    Image.new("RGB", (64, 48), (10, 20, 30)).save(src)
    assert mod._transcode(src, dest, "jpg", 32) is True     # 先转一次，制造"目标已存在"
    assert dest.is_file()

    src.unlink()                                           # 模拟渲染中间件被删

    assert mod._transcode(src, dest, "jpg", 32) is False   # 不抛异常
    assert dest.is_file()                                  # 既有图保留（页面不留破图）


def test_transcode_missing_source_without_dest_is_false(tmp_path):
    assert mod._transcode(tmp_path / "nope.png", tmp_path / "out.jpg",
                          "jpg", None) is False


def test_deliver_survives_missing_render_source(tmp_path):
    """端到端：交付过一次之后再删渲染源，重建必须照常完成（回归 2026-09-23 实测的崩溃）。"""
    src = make_world(tmp_path)
    rc, _ = run_deliver(tmp_path, src)
    assert rc == 0
    # pDirty 在夹具里带人工 fail 裁决 → 落在 failed/；按 glob 取，不写死类别目录
    model_dir = next((tmp_path / "results" / DATE).glob("*/pDirty@smart-topology"))
    images = model_dir / "images"
    assert (images / "base.jpg").is_file()

    # 删掉一个 TRANSCODE 的源（base_iso.png 同时供 thumb.jpg / base.jpg）
    (src / "raw" / "pDirty@smart-topology" / "render" / "base_iso.png").unlink()

    rc, _ = run_deliver(tmp_path, src)

    assert rc == 0                                          # 不崩
    assert (images / "base.jpg").is_file()                  # 既有图仍在
    page = (model_dir / "pDirty@smart-topology.html").read_text(encoding="utf-8")
    assert "检测结论" in page                                # 页面照常生成


def test_subtitle_batch_label_follows_keys():
    """页头批次名按模型键自动判定：写死 T2 会让 standard 批交付的页头说谎。"""
    assert mod._subtitle("2026-09-22", ["p01@smart-topology", "p18@smart-topology"])         == "2026-09-22 · T2 · 2 个模型"
    assert mod._subtitle("2026-09-22", ["p01@standard"])         == "2026-09-22 · standard 对照批 · 1 个模型"
    mixed = mod._subtitle("2026-09-22", ["p01@smart-topology", "p02@standard"])
    assert " + " in mixed and mixed.endswith("2 个模型")
    assert mod._subtitle("2026-09-22", ["pX"]) == "2026-09-22 · 1 个模型"


def test_deliver_places_three_categories_and_transcodes(tmp_path):
    src = make_world(tmp_path)
    rc, _ = run_deliver(tmp_path, src)
    assert rc == 0
    root = tmp_path / "results" / DATE
    # 落位：干净 → passed；人工 fail → failed；GLB 损坏 → 跳过（无 pending/failed）
    assert (root / "passed" / "pClean@smart-topology").is_dir()
    assert (root / "failed" / "pDirty@smart-topology").is_dir()
    assert not (root / "failed" / "pBroken@smart-topology").exists()
    assert not (root / "pending").exists() or not any((root / "pending").iterdir())
    # raw 全量 copy + 图片转码
    m = root / "failed" / "pDirty@smart-topology"
    assert (m / "model.glb").is_file()
    assert (m / "images" / "thumb.jpg").is_file()
    assert not (m / "images" / "hl_front.jpg").exists()   # 判别着色三视图已撤下
    assert not (m / "images" / "overview.jpg").exists()   # 每件一色总览已撤下
    assert (m / "images" / "locator_3.jpg").is_file()     # 定位图（双机位横拼）
    assert (m / "images" / "wire_front.png").is_file()          # 线框保持 PNG
    # 按清单拷贝：render/ 原始中间件不进交付（页面不引用，占体积 93.8%）
    assert not (m / "render").exists()
    # 逐件 GLB 进交付（trimesh 编号的实体，图例按 rank 链到它）
    assert (m / "pieces" / "3.glb").is_file()
    # pDirty 的悬浮件无双引擎 bpy 特写 → 单件隔离渲染三视图条带补位。
    # 宽度反推张数：3 张 300×220 等高横拼 = 3*300 + 2*8 间隔；若把旧命名残留
    # （piece_3_right.png）也卷进来就会变宽——曾出现条带 6 张的事故
    strip = Image.open(m / "images" / "crop_3.jpg")
    assert strip.width == 3 * 300 + 2 * 8, f"条带张数不对（宽度 {strip.width}）"
    # 裁决镜像随模型走
    mirror = json.loads((m / "review.json").read_text(encoding="utf-8")
                        .splitlines()[0])
    assert mirror["disposition"] == DISPOSITION_FAIL
    # 双层 HTML
    assert (root / "index.html").is_file()
    page = (m / "pDirty@smart-topology.html").read_text(encoding="utf-8")
    assert "悬浮碎片" in page and "待人工复核" in page  # 展示层白话文本
    # 贴合界面只在结论句里说明一次（表下不再重复；此前两处各说一遍，读者会以为有两件事）
    assert page.count("部件贴合界面 2 处") == 1
    assert "不计入复核队列" in page
    assert "确定不通过" in page
    # 图证必须被页面**引用**，而不只是文件生成（曾因读取 glob 与写入名不一致，
    # 全部检出行的图证列显示 "—"，而文件断言依旧通过）
    assert 'src="images/crop_3.jpg"' in page
    assert 'src="images/locator_3.jpg"' in page
    assert "<td>—</td>" not in page
    index = (root / "index.html").read_text(encoding="utf-8")
    assert "确定通过（1）" in index and "确定不通过（1）" in index
    assert "部件贴合界面" in index and "导出特性" in index
    assert "待人工裁决（0）" in index                            # fail 仅人工产生
    assert "测试" not in index and "测试" not in page           # 交付措辞红线
    # 附录
    assert (root / "data" / "findings.jsonl").is_file()
    # prompt 定义随交付（任务书要求交代 prompt 选型依据）
    assert (root / "data" / "prompts.jsonl").is_file()
    assert (root / "review.jsonl").is_file()                    # 交付时即复核档案


def test_deliver_dry_run_writes_nothing(tmp_path):
    src = make_world(tmp_path)
    rc, _ = run_deliver(tmp_path, src, dry=True)
    assert rc == 0
    assert not (tmp_path / "results" / DATE).exists()           # 零写入


def test_deliver_rerun_idempotent_keeps_human_review(tmp_path):
    src = make_world(tmp_path)
    run_deliver(tmp_path, src)
    # 人工改判 pClean → 不通过，再重跑：落位跟随 review.jsonl，不被规则覆盖
    from meshq.core.review import upsert_review
    upsert_review(src / "review.jsonl", "pClean@smart-topology",
                  DISPOSITION_FAIL, reason="复核推翻")
    rc, _ = run_deliver(tmp_path, src)
    assert rc == 0
    root = tmp_path / "results" / DATE
    assert (root / "failed" / "pClean@smart-topology").is_dir()
    assert not (root / "passed" / "pClean@smart-topology").exists()
    mirror = json.loads((root / "failed" / "pClean@smart-topology" / "review.json")
                        .read_text(encoding="utf-8").splitlines()[0])
    assert mirror["reason"] == "复核推翻"


def test_deliver_batch_filter(tmp_path):
    src = make_world(tmp_path)
    rc, _ = run_deliver(tmp_path, src, "--batch", "standard")
    assert rc == 0
    assert not (tmp_path / "results" / DATE).exists()           # 无 standard 键可交付


def test_index_card_keeps_composition_when_verdict_reason_empty(tmp_path):
    """裁决理由可为空：此时卡片副标题必须回退到问题构成，不能变成空行。

    2026-09-23 实测暴露：把人的 reason 直接当副标题后，一次"维持待定"（未填理由）的裁决
    让队列页那张卡片的副标题变成空白——规则落位时显示的是问题构成，人工裁决后反而什么都没有。
    """
    src = make_world(tmp_path)
    # 覆盖掉带理由的裁决：改成"维持待定"+空理由（模拟审核台的默认提交）
    write_reviews(src / "review.jsonl",
                  {"pDirty@smart-topology": make_record(
                      "pDirty@smart-topology", DISPOSITION_PENDING, reason="",
                      reviewed_at="2026-09-23T12:23:28Z")})

    rc, _ = run_deliver(tmp_path, src)

    assert rc == 0
    card = [l for l in (tmp_path / "results" / DATE / "index.html")
            .read_text(encoding="utf-8").splitlines()
            if 'class="meta"' in l and "@smart-topology" not in l]
    assert card, "队列页应仍有卡片副标题行"
    assert any("悬浮 1 条" in l for l in card)     # 回退到问题构成
