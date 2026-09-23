import json
from pathlib import Path

from PIL import Image

import meshq.stages.render as rend
ROOT = Path(__file__).resolve().parent.parent


def make_cfg(solid="#3C4048"):
    return {"render": {"resolution": [1280, 960],
                       "lookdev": {"bg_solid_color": solid}}}


def test_build_cmd_shape(tmp_path):
    exe = tmp_path / "blender.exe"
    cmd = rend.build_cmd(exe, Path("in.glb"), Path("out"), Path("checks.json"),
                         (1280, 960), 0.10, {"wire_thickness": 0.002})
    assert cmd[1:5] == ["--background", "--python-exit-code", "1", "--python"]
    assert str(rend.RENDER_ONE) in cmd
    assert "--" in cmd
    tail = cmd[cmd.index("--") + 1:]
    assert tail[0] == "in.glb" and tail[2] == "checks.json"
    assert tail[3:6] == ["1280", "960", "0.1"]
    # lookdev 参数以 JSON 透传给 render_one（PLAN-02：参数进 config 不硬编码）
    assert json.loads(tail[6])["wire_thickness"] == 0.002


def test_find_models_empty_when_no_data(tmp_path):
    assert rend.find_models(tmp_path) == []


def test_find_models_picks_keys_with_glb(tmp_path):
    raw = tmp_path / "raw"
    (raw / "p01@standard").mkdir(parents=True)
    (raw / "p01@standard" / "model.glb").touch()
    (raw / "p02@standard").mkdir()  # 无 glb，不应入选
    assert rend.find_models(tmp_path) == ["p01@standard"]


def test_outputs_complete_requires_checks_and_all_passes(tmp_path):
    rd = tmp_path / "p01" / "render"
    rd.mkdir(parents=True)
    (tmp_path / "p01" / "blender_checks.json").write_text("{}", encoding="utf-8")
    assert not rend.outputs_complete(tmp_path / "p01")
    (rd / "base_iso.png").touch()
    for v in ("front", "top", "right"):
        (rd / f"base_{v}.png").touch()
        (rd / f"highlight_{v}.png").touch()
        (rd / f"wire_{v}.png").touch()
    assert rend.outputs_complete(tmp_path / "p01")
    (rd / "wire_top.png").unlink()
    assert not rend.outputs_complete(tmp_path / "p01")


def test_stitch_compare_side_by_side(tmp_path):
    smooth = tmp_path / "base_front.png"
    wire = tmp_path / "wire_front.png"
    Image.new("RGB", (100, 80), (200, 0, 0)).save(smooth)
    Image.new("RGB", (100, 60), (0, 0, 200)).save(wire)
    dest = tmp_path / "compare_front.png"

    rend.stitch_compare(smooth, wire, dest, (60, 64, 72))

    img = Image.open(dest)
    assert img.size == (208, 60)  # 100 + 8 间隔 + 100，取两图较矮高度
    assert img.getpixel((103, 30)) == (60, 64, 72)  # 接缝底色


def test_build_tiers_maps_trimesh_rank_to_bpy_rank(tmp_path, monkeypatch):
    """档位映射经双引擎匹配转接：判别 rank(trimesh) → engine_bpy_rank；缺表 = 空。"""
    data = tmp_path
    (data / "parts.jsonl").write_text(json.dumps(
        {"key": "pX@smart-topology",
         "parts": [{"rank": 3, "engine_bpy_rank": 7},
                   {"rank": 4, "engine_bpy_rank": 9},
                   {"rank": 5, "engine_bpy_rank": None}]}) + "\n", encoding="utf-8")
    (data / "part_verdicts.jsonl").write_text(json.dumps(
        {"key": "pX@smart-topology",
         "decisions": [{"rank": 3, "tier": "C"}, {"rank": 5, "tier": "C"}]}) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(rend, "DATA_DIR", data)

    tiers = rend.build_tiers("pX@smart-topology")

    assert tiers == {7: "C"}  # rank5 无 bpy 对应（共位重复件）→ 无法着色，安全略过
    # 判别表缺失 → 空映射（高亮全灰，诚实呈现）
    monkeypatch.setattr(rend, "DATA_DIR", tmp_path / "none")
    assert rend.build_tiers("pX@smart-topology") == {}


def test_post_process_stitches_and_tolerates_missing(tmp_path):
    rd = tmp_path / "render"
    rd.mkdir(parents=True)
    Image.new("RGB", (1280, 960), (200, 0, 0)).save(rd / "base_front.png")
    Image.new("RGB", (1280, 960), (0, 0, 200)).save(rd / "wire_front.png")
    (tmp_path / "checks.json").write_text("{}", encoding="utf-8")

    made = rend.post_process(rd, tmp_path / "checks.json", (1280, 960), make_cfg())

    # 未给 compare_left（模型未修复）→ 不产对比图
    assert "compare_front.png" not in made
    # 给 compare_left → 产出"修复前 | 修复后"线框对比
    made = rend.post_process(rd, tmp_path / "checks.json", (1280, 960), make_cfg(),
                             compare_left=rd / "base_front.png")
    assert "compare_front.png" in made
    assert (rd / "compare_front.png").is_file()


def test_post_process_downscale_wire_when_ssaa(tmp_path):
    rd = tmp_path / "render"
    rd.mkdir(parents=True)
    Image.new("RGB", (2560, 1920), (0, 0, 200)).save(rd / "wire_front.png")
    Image.new("RGB", (1280, 960), (200, 0, 0)).save(rd / "base_front.png")
    (tmp_path / "checks.json").write_text(json.dumps(
        {"wire_display": {"ssaa": 2}}), encoding="utf-8")

    rend.post_process(rd, tmp_path / "checks.json", (1280, 960), make_cfg())

    assert Image.open(rd / "wire_front.png").size == (1280, 960)


def test_clean_legacy_keeps_expected_only(tmp_path):
    raw = tmp_path / "raw" / "p01@standard"
    rd = raw / "render"
    rd.mkdir(parents=True)
    (raw / "model.glb").touch()
    keep = [rd / "base_iso.png", rd / "compare_front.png",
            rd / "highlight_front.png"]
    junk = [rd / "front.png", rd / "back.png", rd / "iso.png",
            rd / "highlight_back.png"]
    for f in keep + junk:
        f.touch()

    removed = rend.clean_legacy(tmp_path)

    assert sorted(removed) == sorted(f"p01@standard/{p.name}" for p in junk)
    assert all(p.is_file() for p in keep)
    assert all(not p.exists() for p in junk)


def test_clean_legacy_keeps_every_current_product_family(tmp_path):
    """每个现役图族都要留：本名单曾是手写常量且只登记了 base/compare/zoom，
    于是一次 `--clean-old` 把整族 piece_/locator_ 图当旧残留删掉（实测 1345 张）。"""
    raw = tmp_path / "raw" / "p01@smart-topology"
    rd = raw / "render"
    rd.mkdir(parents=True)
    (raw / "model.glb").touch()
    keep = [rd / "base_front.png", rd / "base_top.png", rd / "base_right.png",
            rd / "base_iso.png", rd / "highlight_top.png", rd / "wire_right.png",
            rd / "compare_front.png",
            rd / "piece_1_face.png", rd / "piece_12_third.png",
            rd / "locator_3_model.png", rd / "locator_94_close.png"]
    junk = [rd / "review_sheet.png", rd / "parts_front.png",
            rd / "highlight_front_zoom.png",     # 缺陷放大图：逐检出定位图上线后退役
            rd / "piece_1_front.png",            # 旧单件机位命名
            rd / "locator_3.png"]
    for f in keep + junk:
        f.touch()

    removed = rend.clean_legacy(tmp_path)

    assert sorted(removed) == sorted(f"p01@smart-topology/{p.name}" for p in junk)
    assert all(p.is_file() for p in keep)


def test_clean_legacy_leaves_json_products(tmp_path):
    """清理只作用于 png：json 产物（完成标记、定位数据）必须原样留下。"""
    raw = tmp_path / "raw" / "p01@smart-topology"
    rd = raw / "render"
    rd.mkdir(parents=True)
    (raw / "model.glb").touch()
    jsons = [rd / "locator_marks.json", rd / "locator.json"]
    for f in jsons:
        f.write_text("{}", encoding="utf-8")

    rend.clean_legacy(tmp_path)

    assert all(p.is_file() for p in jsons)


def test_hex_rgb():
    assert rend.hex_rgb("#3C4048") == (60, 64, 72)
    assert rend.hex_rgb("FFFFFF") == (255, 255, 255)


# ---------------------------------------------------------- 组件配色诊断（PLAN-03 §6.1）

def test_build_cmd_appends_parts_json_only_when_given(tmp_path):
    base = rend.build_cmd(tmp_path / "b.exe", Path("in.glb"), Path("out"),
                          Path("checks.json"), (1280, 960), 0.1, {})
    assert len(base[base.index("--") + 1:]) == 7          # 无 parts ⇒ 不影响既有契约

    parts = {"channels": ["parts"], "colors": ["#4E79A7", "#F28E2B"]}
    cmd = rend.build_cmd(tmp_path / "b.exe", Path("in.glb"), Path("out"),
                         Path("checks.json"), (1280, 960), 0.1, {}, parts)
    tail = cmd[cmd.index("--") + 1:]
    assert len(tail) == 8
    assert json.loads(tail[7])["colors"] == ["#4E79A7", "#F28E2B"]


def test_main_survives_single_model_timeout(tmp_path, monkeypatch):
    """单模型超时只记入 failed，批处理继续跑完并整体返回 1（不炸全批）。"""
    import subprocess

    raw = tmp_path / "raw" / "pX@standard"
    raw.mkdir(parents=True)
    # 真 GLB（可被 trimesh 加载）：piece store 现在无条件构建（PLAN-06 §九修订①），
    # 头合法的空壳 GLB 过得了 glb_valid，却会在 store 的 weld+split 时炸
    import trimesh
    trimesh.creation.box(extents=(1, 1, 1)).export(raw / "model.glb")
    monkeypatch.setattr(rend, "DATA_DIR", tmp_path)
    monkeypatch.setattr(rend, "load_config", lambda: {
        "blender": {"path": str(tmp_path / "b.exe")},
        "detect": {"diag_ratio": 0.1},
        "render": {"resolution": [1280, 960], "timeout_seconds": 1,
                   "lookdev": {"bg_solid_color": "#3C4048"}}})
    monkeypatch.setattr(rend, "get_blender_exe",
                        lambda cfg: tmp_path / "b.exe")

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="blender", timeout=1)

    monkeypatch.setattr(rend.subprocess, "run", raise_timeout)

    assert rend.main(["--keys", "pX@standard"]) == 1


def test_build_tiers_v2_findings_take_precedence(tmp_path, monkeypatch):
    """v2 口径：findings 待复核检出决定着色（悬浮→A、重叠/非核心→C、keep 不亮）；
    findings 缺失时回退级联（冻结口径，由既有测试覆盖）。"""
    data = tmp_path
    (data / "parts.jsonl").write_text(json.dumps(
        {"key": "pX@smart-topology",
         "parts": [{"rank": 1, "engine_bpy_rank": 2},
                   {"rank": 2, "engine_bpy_rank": 4},
                   {"rank": 3, "engine_bpy_rank": 6},
                   {"rank": 4, "engine_bpy_rank": 8}]}) + "\n", encoding="utf-8")
    (data / "part_verdicts.jsonl").write_text(json.dumps(
        {"key": "pX@smart-topology",
         "decisions": [{"rank": 1, "tier": "C"}]}) + "\n", encoding="utf-8")
    from meshq.core.findings import make_finding
    rows = [
        make_finding(key="pX@smart-topology", detector="islands",
                     defect_class="floating", rank=1, reason="r"),
        make_finding(key="pX@smart-topology", detector="co_located",
                     defect_class="co_located", rank=2, reason="r"),
        make_finding(key="pX@smart-topology", detector="co_located",
                     defect_class="co_located", rank=3, tier="keep", reason="r"),
        make_finding(key="pX@smart-topology", detector="triage",
                     defect_class="other", rank=4, reason="r"),
    ]
    (data / "findings.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(rend, "DATA_DIR", data)

    assert rend.build_tiers("pX@smart-topology") == {2: "A", 4: "C", 8: "C"}


# ---------------------------------------------------------------- 渲染目标化（PLAN-05 P2）


def test_target_channels_table():
    assert rend.target_channels("overview") == ["base"]
    assert rend.target_channels("highlight") == ["highlight"]
    assert rend.target_channels("wire") == ["wire"]
    assert rend.target_channels("pieces") == []
    assert rend.target_channels("locator") == []
    assert rend.target_channels("all") == ["base", "highlight", "wire"]


def test_target_complete_per_target(tmp_path):
    raw = tmp_path / "raw" / "pX"
    render = raw / "render"
    png = lambda p: p.parent.mkdir(parents=True, exist_ok=True) or \
        __import__("PIL.Image", fromlist=["Image"]).new("RGB", (64, 48)).save(p)
    assert rend.target_complete(raw, "overview") is False
    png(render / "base_front.png")
    png(render / "base_iso.png")
    assert rend.target_complete(raw, "overview") is True
    assert rend.target_complete(raw, "all") is False            # 高亮/线框未出
    png(render / "highlight_front.png")
    png(render / "wire_front.png")
    (render / "blender_checks.json").write_text("{}", encoding="utf-8")
    assert rend.target_complete(raw, "all") is True
    assert rend.target_complete(raw, "pieces") is False         # pieces 视为显式意图，总是重跑


def test_resolve_target_legacy_flags_map():
    import types
    def ns(**kw):
        base = {"target": None, "pieces_only": False}
        base.update(kw)
        return types.SimpleNamespace(**base)
    assert rend.resolve_target(ns()) == "all"
    assert rend.resolve_target(ns(pieces_only=True)) == "pieces"
    assert rend.resolve_target(ns(target="overview")) == "overview"  # 新旗标优先


def test_export_review_pieces_uses_store(tmp_path, monkeypatch):
    """export_review_pieces 从 piece store 取单件 GLB，不再重复 weld+split。"""
    import json

    import trimesh

    from meshq.core.piece_store import ensure_store

    data = tmp_path / "data"
    key = "pX@smart-topology"
    model = data / "raw" / key / "model.glb"
    model.parent.mkdir(parents=True, exist_ok=True)
    meshes = [trimesh.creation.box(extents=(1, 1, 1)).apply_translation((i * 5.0, 0, 0))
              for i in range(3)]
    trimesh.util.concatenate(meshes).export(model)
    from meshq.core.findings import make_finding
    rows = [make_finding(key=key, detector="islands", defect_class="floating",
                         rank=2, reason="r")]
    (data / "findings.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(rend, "DATA_DIR", data)
    # 该路径要读 config 的 parts.weld_digits：干净检出（CI）里没有 config.toml，
    # 必须像其他用例一样注入，否则测试依赖本机未追踪文件（实测 CI 会红）
    monkeypatch.setattr(rend, "load_config", lambda: {"parts": {"weld_digits": 5}})

    out = rend.export_review_pieces(key, data / "raw" / key / "render")

    assert [p.name for p in out] == ["2.glb"]                    # 只导待复核 rank
    pieces = data / "raw" / key / "pieces"
    assert (pieces / "manifest.json").is_file()
    assert len(list(pieces.glob("*.glb"))) == 3                  # store 覆盖全部组件


def test_export_review_pieces_builds_store_even_without_findings(tmp_path, monkeypatch):
    """无待复核检出 ≠ 不建 store（PLAN-06 §九修订①）。

    此前该函数在"该模型无 review 行"时提前 return，三个"确定通过"的模型因此没有任何
    pieces/ 产物——渲染端改逐件导入时会无件可导，而它们恰恰是当前通过的模型。
    """
    import json

    import trimesh

    data = tmp_path / "data"
    key = "pClean@smart-topology"
    model = data / "raw" / key / "model.glb"
    model.parent.mkdir(parents=True, exist_ok=True)
    meshes = [trimesh.creation.box(extents=(1, 1, 1)).apply_translation((i * 5.0, 0, 0))
              for i in range(3)]
    trimesh.util.concatenate(meshes).export(model)
    from meshq.core.findings import make_finding
    rows = [make_finding(key=key, detector="co_located", defect_class="co_located",
                         rank=1, tier="keep", reason="界面贴合",
                         evidence={"co_located_subtype": "interface"},
                         metrics={"overlap_frac": 0.05})]
    (data / "findings.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(rend, "DATA_DIR", data)
    monkeypatch.setattr(rend, "load_config", lambda: {"parts": {"weld_digits": 5}})

    out = rend.export_review_pieces(key, data / "raw" / key / "render")

    assert out == []                                     # 无待复核件 → 不产单件渲染
    pieces = data / "raw" / key / "pieces"
    assert (pieces / "manifest.json").is_file()          # 但 store 必须已建
    assert len(list(pieces.glob("*.glb"))) == 3


