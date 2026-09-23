"""seed_data：数据分发与装载的守卫。

覆盖：分组展开（glob 去重）、清单指纹（size + sha256 + GLB 校验）、打包→装载闭环
（含篡改检测）、目录形态的装载、体检报告口径。
"""

import hashlib
import json
import zipfile

from fakes import write_valid_glb
import meshq.tools.seed_data as seed_data
def make_world(tmp_path):
    """最小 data/：一个模型（含 meta）、一个渲染中间件、一个分析产物。"""
    raw = tmp_path / "data" / "raw" / "pA@smart-topology"
    write_valid_glb(raw / "model.glb")
    (raw / "meta.json").write_text('{"prompt_id": "A"}', encoding="utf-8")
    (raw / "render").mkdir()
    (raw / "render" / "base_iso.png").write_bytes(b"png-bytes")
    (tmp_path / "data" / "findings.jsonl").write_text('{"key": "pA"}\n', encoding="utf-8")
    (tmp_path / "prompts.jsonl").write_text('{"pid": "A"}\n', encoding="utf-8")
    return tmp_path


def test_group_files_expands_and_dedupes(tmp_path):
    make_world(tmp_path)

    models = seed_data.group_files("models", tmp_path)
    rels = [p.relative_to(tmp_path).as_posix() for p in models]

    assert rels == ["data/raw/pA@smart-topology/meta.json",
                    "data/raw/pA@smart-topology/model.glb"]       # 排序稳定、无重复
    analysis = seed_data.group_files("analysis", tmp_path)
    assert "prompts.jsonl" in [p.relative_to(tmp_path).as_posix() for p in analysis]


def test_manifest_records_sha256_and_glb_validity(tmp_path):
    make_world(tmp_path)
    files = seed_data.group_files("models", tmp_path)

    manifest = seed_data.build_manifest(
        [(f.relative_to(tmp_path).as_posix(), f) for f in files])
    by_path = {e["path"]: e for e in manifest["files"]}
    glb = by_path["data/raw/pA@smart-topology/model.glb"]

    assert glb["glb"] == "valid"
    assert glb["sha256"] == hashlib.sha256(
        (tmp_path / glb["path"]).read_bytes()).hexdigest()


def test_pack_then_unpack_roundtrip(tmp_path):
    make_world(tmp_path)
    out = tmp_path / "dist"

    made = seed_data.pack(["models", "analysis"], out, root=tmp_path)
    assert [p.name for p in made] == ["mesh-data-models.zip", "mesh-data-analysis.zip"]
    with zipfile.ZipFile(made[0]) as z:
        assert seed_data.MANIFEST_NAME in z.namelist()

    fresh = tmp_path / "fresh"            # 空仓库根，装载后应与源一致
    fresh.mkdir()
    assert seed_data.unpack(made, root=fresh) == 0
    assert (fresh / "data" / "raw" / "pA@smart-topology" / "model.glb").is_file()
    assert (fresh / "prompts.jsonl").is_file()


def test_unpack_detects_corruption(tmp_path):
    make_world(tmp_path)
    made = seed_data.pack(["analysis"], tmp_path / "dist", root=tmp_path)
    # 篡改包内容（模拟传输损坏）：清单里的 sha256 与解出的字节不再匹配
    tampered = tmp_path / "dist" / "tampered.zip"
    with zipfile.ZipFile(made[0]) as src, zipfile.ZipFile(tampered, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if name.endswith(".jsonl"):
                data = data.replace(b"A", b"B")
            dst.writestr(name, data)

    rc = seed_data.unpack([tampered], root=tmp_path / "fresh2")

    assert rc == 1                                        # 校验失败必须报错
    assert not (tmp_path / "fresh2" / "prompts.jsonl").exists()


def test_unpack_accepts_directory_of_packs(tmp_path):
    make_world(tmp_path)
    out = tmp_path / "dist"
    seed_data.pack(["models"], out, root=tmp_path)

    rc = seed_data.main(["--unpack", str(out), "--dry-run"])

    assert rc == 0                                        # 目录形态自动收 *.zip


def test_check_reports_missing_inputs(tmp_path, capsys):
    # 空仓库：所有阶段输入都缺
    (tmp_path / "prompts.jsonl").write_text("", encoding="utf-8")

    missing = seed_data.check(tmp_path, cfg={"blender": {"path": ""}})
    out = capsys.readouterr().out

    assert missing > 0
    assert "缺" in out and "--unpack" in out


def test_check_passes_when_data_present(tmp_path, capsys):
    make_world(tmp_path)
    raw = tmp_path / "data" / "raw" / "pA@smart-topology"
    (tmp_path / "data" / "fixed").mkdir()
    (tmp_path / "publish").mkdir()
    (tmp_path / "data" / "review.jsonl").write_text("", encoding="utf-8")

    missing = seed_data.check(tmp_path, cfg={"blender": {"path": str(raw)}})

    assert missing == 0

# ---------------------------------------------------------------- 批次范围

def make_two_batches(tmp_path):
    """两个批次的模型 + 混合记录的分析产物。"""
    for key in ("pA@smart-topology", "pB@standard"):
        d = tmp_path / "data" / "raw" / key
        write_valid_glb(d / "model.glb")
        (d / "meta.json").write_text('{"pid": "x"}', encoding="utf-8")
        (d / "render").mkdir(parents=True)
        (d / "render" / "base_iso.png").write_bytes(b"png")
    (tmp_path / "data" / "findings.jsonl").write_text(
        '{"key": "pA@smart-topology", "tier": "review"}' + chr(10)
        + '{"key": "pB@standard", "tier": "review"}' + chr(10), encoding="utf-8")
    (tmp_path / "data" / "metrics.jsonl").write_text(
        '{"key": "pA@smart-topology"}' + chr(10) + '{"key": "pB@standard"}' + chr(10),
        encoding="utf-8")
    for name, batch in (("summary_t2.json", "t2"), ("summary-standard-v2.json", "std")):
        (tmp_path / "data" / name).write_text("{}", encoding="utf-8")
    (tmp_path / "prompts.jsonl").write_text('{"pid": "x"}', encoding="utf-8")
    return tmp_path


def test_group_files_filters_by_batch(tmp_path):
    """路径型分组按目录键的档位后缀过滤；无后缀的键（探针）不属于任何批次。"""
    root = make_two_batches(tmp_path)
    (root / "data" / "raw" / "probe-poly15k").mkdir(parents=True)
    write_valid_glb(root / "data" / "raw" / "probe-poly15k" / "model.glb")

    t2 = [p.relative_to(root).as_posix() for p in seed_data.group_files("models", root, "smart-topology")]
    std = [p.relative_to(root).as_posix() for p in seed_data.group_files("models", root, "standard")]
    allf = [p.relative_to(root).as_posix() for p in seed_data.group_files("models", root)]

    assert t2 == ["data/raw/pA@smart-topology/meta.json", "data/raw/pA@smart-topology/model.glb"]
    assert std == ["data/raw/pB@standard/meta.json", "data/raw/pB@standard/model.glb"]
    assert len(allf) == 5                    # 含探针：两批 × 2 + 探针 × 1（夹具里探针只有 glb）
    assert all("probe" not in p for p in t2 + std)


def test_analysis_entries_trim_records_and_batch_files(tmp_path):
    """分析产物逐记录裁剪 + 只带该批次的 summary 文件（prompts 与无键文件原样带）。"""
    root = make_two_batches(tmp_path)
    stage = tmp_path / "_stage"
    stage.mkdir()

    got = seed_data.analysis_entries(root, "smart-topology", stage)
    rels = [rel for rel, _ in got]

    assert "data/findings.jsonl" in rels and "data/metrics.jsonl" in rels
    assert "data/summary_t2.json" in rels
    assert "data/summary-standard-v2.json" not in rels      # 别的批次
    assert "prompts.jsonl" in rels                           # 与批次无关
    body = {rel: p for rel, p in got}["data/findings.jsonl"].read_text(encoding="utf-8")
    assert "pA@smart-topology" in body and "pB@standard" not in body


def test_pack_with_batch_only_includes_that_batch(tmp_path):
    """端到端：按 T2 打包后，standard 的模型与记录都不在包里。"""
    root = make_two_batches(tmp_path)
    out = tmp_path / "dist"

    seed_data.pack(["models", "analysis"], out, root=root, batch="smart-topology")

    import zipfile
    with zipfile.ZipFile(out / "mesh-data-models.zip") as z:
        names = z.namelist()
    assert any("pA@smart-topology" in n for n in names)
    assert not any("pB@standard" in n for n in names)
    with zipfile.ZipFile(out / "mesh-data-analysis.zip") as z:
        findings = z.read("data/findings.jsonl").decode("utf-8")
        assert "pA@smart-topology" in findings and "pB@standard" not in findings
