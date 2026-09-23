"""pipeline：总入口编排的守卫（PLAN-05 §五触发入口）。

只测 dry-run 路径与编排语义（不触网络/Blender/写盘）：
- 每阶段 dry-run 打印等效命令且返回 0；
- generate 干跑的 credits 预估按台账增量计算；
- deliver 干跑复用落位规则（review 优先、界面贴合不计入）；
- all 按序串 detect → report → deliver，且 detect 透传不含 --date。
"""

import json

import meshq.pipeline as pipeline
import pytest


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """fake data 目录：prompts/tasks/findings/raw 两模型（一带检出一带干净）。"""
    (tmp_path / "raw" / "pClean@smart-topology").mkdir(parents=True)
    (tmp_path / "raw" / "pClean@smart-topology" / "model.glb").write_bytes(b"x")
    (tmp_path / "raw" / "pDirty@smart-topology").mkdir(parents=True)
    (tmp_path / "raw" / "pDirty@smart-topology" / "model.glb").write_bytes(b"x")
    (tmp_path / "raw" / "pDirty@smart-topology" / "render").mkdir()
    (tmp_path / "prompts.jsonl").write_text(
        json.dumps({"pid": "p01", "prompt": "chair"}) + "\n", encoding="utf-8")
    (tmp_path / "tasks.json").write_text(json.dumps([
        {"key": "p01@standard", "status": "SUCCEEDED"},
    ]), encoding="utf-8")
    from meshq.core.findings import make_finding
    rows = [make_finding(key="pDirty@smart-topology", detector="islands",
                         defect_class="floating", rank=1, reason="r")]
    (tmp_path / "findings.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    return tmp_path


def run(capsys, *argv):
    rc = pipeline.main(list(argv))
    return rc, capsys.readouterr().out


def test_detect_dry_run_prints_chain_without_date(tmp_path, world, capsys):
    rc, out = run(capsys, "detect", "--dry-run", "--date", "2026-09-22")
    assert rc == 0
    assert "WOULD RUN: python -m meshq.stages.metrics" in out
    assert "meshq.core.part_features" in out and "meshq.core.part_verdict" in out
    assert "meshq.stages.detect" in out
    assert "--date" not in out                       # detect 不认识 --date，不透传
    assert "findings.jsonl" in out                   # 影响面：将写出的产物


def test_generate_dry_run_estimates_credits(world, capsys):
    rc, out = run(capsys, "generate", "--dry-run", "--all", "--preset",
                  "smart-topology")
    assert rc == 0
    # p01@smart-topology 未在台账 → 1 个新任务 × 5 credits
    assert "1 个待新建" in out and "5 credits" in out
    assert "meshq.stages.generate --all --preset smart-topology" in out


def test_generate_dry_run_skips_succeeded(world, capsys):
    rc, out = run(capsys, "generate", "--dry-run", "--all", "--preset", "standard")
    assert rc == 0
    assert "0 个待新建" in out and "0 credits" in out   # p01@standard 已 SUCCEEDED


def test_render_dry_run_lists_missing(world, capsys):
    rc, out = run(capsys, "render", "--dry-run")
    assert rc == 0
    assert "2/2 个模型缺渲染产物" in out              # 两个 fake 模型都无 render


def test_deliver_dry_run_uses_disposition_rule(world, capsys):
    rc, out = run(capsys, "deliver", "--dry-run", "--date", "2026-09-22")
    assert rc == 0
    assert "2 个模型 →" in out
    assert "pass 1" in out and "pending 1" in out    # 落位规则：界面/无检出不计
    assert "results/2026-09-22/" in out
    assert "meshq.stages.deliver" in out


def test_all_dry_run_chains_three_stages(world, capsys):
    rc, out = run(capsys, "all", "--dry-run", "--batch", "smart-topology",
                  "--date", "2026-09-22")
    assert rc == 0
    assert "meshq.stages.metrics" in out and "meshq.stages.detect" in out
    assert "meshq.stages.report --batch smart-topology" in out
    assert "meshq.stages.report --batch standard" in out
    assert "meshq.stages.deliver" in out
    # detect 段透传 --batch 但不透传 --date；deliver 段两者都要
    detect_block = out.split("meshq.stages.report")[0]
    assert detect_block.count("--date") == 0
    assert "--batch smart-topology" in out


def test_render_target_passthrough(world, capsys):
    rc, out = run(capsys, "render", "--dry-run", "--target", "pieces")
    assert rc == 0
    assert "--target pieces" in out
