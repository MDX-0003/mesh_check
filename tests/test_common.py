import json
import tomllib
from itertools import islice
from pathlib import Path

import pytest
import requests

from meshq.core.common import (
    MODEL_PRESETS,
    ConfigError,
    MeshyClient,
    TaskLedger,
    build_task_payload,
    get_api_key,
    get_blender_exe,
    load_config,
    load_prompts,
    iter_jsonl,
    read_jsonl,
    read_jsonl_by_key,
)
from tests.fakes import FakeResponse, FakeSession

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- config

def test_load_config_from_example():
    cfg = load_config(ROOT / "config.example.toml")
    # 模板是给接收方照抄的：`[blender] path` 刻意留空（不带作者的机器路径）；
    # 其余结构性字段必须齐全，且能被加载器接受。
    assert cfg["blender"]["path"] == ""
    assert cfg["detect"]["diag_ratio"] == pytest.approx(0.10)


def test_get_blender_exe_empty_path_message():
    """path 为空时报"未填写"，而不是把它退化成 `.` 说"文件不存在"（实测踩过）。"""
    with pytest.raises(ConfigError, match="未填写"):
        get_blender_exe({"blender": {"path": ""}})


def test_get_blender_exe_missing_file_message(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        get_blender_exe({"blender": {"path": str(tmp_path / "nope.exe")}})


def test_load_config_missing_raises(tmp_path):
    with pytest.raises(ConfigError, match="cp config.example.toml"):
        load_config(tmp_path / "config.toml")


def test_load_config_missing_section_raises(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[blender]\npath = 'x'\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="meshy"):
        load_config(p)


def test_get_api_key_config_wins_over_env():
    cfg = {"meshy": {"api_key": "cfg-key"}}
    assert get_api_key(cfg, env={"MESHY_API_KEY": "env-key"}) == "cfg-key"


def test_get_api_key_env_fallback():
    assert get_api_key({"meshy": {}}, env={"MESHY_API_KEY": "env-key"}) == "env-key"


def test_get_api_key_missing_raises():
    with pytest.raises(ConfigError, match="MESHY_API_KEY"):
        get_api_key({"meshy": {}}, env={})


def test_example_and_local_config_structurally_identical():
    """本地 config.toml（若存在）除密钥外应与模板同构，防止手改漂移。"""
    local = ROOT / "config.toml"
    ex = tomllib.loads((ROOT / "config.example.toml").read_text(encoding="utf-8"))
    if not local.exists():
        pytest.skip("config.toml 未创建")
    lc = tomllib.loads(local.read_text(encoding="utf-8"))
    assert set(lc) == set(ex)
    for sec in ex:
        assert set(lc[sec]) == set(ex[sec]), f"[{sec}] 键集合漂移"


# ---------------------------------------------------------------- prompts

def test_load_prompts_reads_20_records():
    items = load_prompts()
    assert len(items) == 20
    assert items["p17"]["expect_floater"] is True
    assert len([p for p in items.values() if p["expect_floater"]]) == 4


def test_load_prompts_rejects_duplicate_pid(tmp_path):
    p = tmp_path / "prompts.jsonl"
    p.write_text('{"pid":"a","prompt":"x"}\n{"pid":"a","prompt":"y"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="重复"):
        load_prompts(p)


# ---------------------------------------------------------------- payload

def test_build_task_payload_standard():
    payload = build_task_payload("a chair", "standard")
    assert payload["mode"] == "preview"
    assert payload["should_remesh"] is False
    assert payload["ai_model"] == "meshy-7"
    assert payload["target_formats"] == ["glb"]


def test_build_task_payload_smart_topology_has_no_remesh_flag():
    payload = build_task_payload("a chair", "smart-topology")
    assert "should_remesh" not in payload
    assert payload["ai_model"] == "meshy-t2"


def test_build_task_payload_unknown_preset_and_long_prompt():
    with pytest.raises(ConfigError):
        build_task_payload("x", "ultra")
    with pytest.raises(ValueError):
        build_task_payload("x" * 801, "standard")


def test_presets_documented_models():
    assert set(MODEL_PRESETS) == {"standard", "smart-topology"}


# ---------------------------------------------------------------- client

def make_client(responses: dict) -> MeshyClient:
    return MeshyClient("test-key", session=FakeSession(responses))


def test_client_create_returns_id():
    c = make_client({"/text-to-3d": FakeResponse(200, {"id": "task-1"})})
    assert c.create_task({"prompt": "x"}) == "task-1"
    assert c._http.calls[0][0] == "POST"


def test_client_create_accepts_result_form():
    c = make_client({"/text-to-3d": FakeResponse(200, {"result": "task-2"})})
    assert c.create_task({}) == "task-2"


def test_client_get_task():
    c = make_client({"/text-to-3d/t9": FakeResponse(200, {"id": "t9", "status": "SUCCEEDED"})})
    assert c.get_task("t9")["status"] == "SUCCEEDED"


def test_client_delete_tolerates_204_and_raises_500():
    c = make_client({"/text-to-3d/t1": FakeResponse(204)})
    c.delete_task("t1")  # 不抛
    c = make_client({"/text-to-3d/t2": FakeResponse(500, {"message": "boom"})})
    with pytest.raises(requests.HTTPError):
        c.delete_task("t2")


def test_client_create_error_propagates():
    c = make_client({"/text-to-3d": FakeResponse(402, {"message": "out of credits"})})
    with pytest.raises(requests.HTTPError):
        c.create_task({})


# ---------------------------------------------------------------- ledger

def test_ledger_roundtrip(tmp_path):
    p = tmp_path / "tasks.json"
    led = TaskLedger(p)
    led.update("p01@standard", task_id="t1", status="SUCCEEDED", credits=5)
    led2 = TaskLedger(p)
    assert led2.get("p01@standard")["credits"] == 5


def test_ledger_needs_task_state_machine(tmp_path):
    led = TaskLedger(tmp_path / "tasks.json")
    assert led.needs_task("k") is True  # 无记录
    led.update("k", status="SUCCEEDED")
    assert led.needs_task("k") is False  # 完成 → 跳过
    led.update("k", status="FAILED")
    assert led.needs_task("k") is True  # 失败 → 重建
    led.update("k", status="PENDING", task_id="t")
    assert led.needs_task("k") is False  # 进行中 → 续查
    led.update("k", status="IN_PROGRESS", task_id=None)
    assert led.needs_task("k") is True  # 态在但没 id → 重建


def test_ledger_summary(tmp_path):
    led = TaskLedger(tmp_path / "t.json")
    led.update("a", status="SUCCEEDED")
    led.update("b", status="FAILED")
    assert led.summary() == {"SUCCEEDED": 1, "FAILED": 1}


def test_jsonl_exports_stay_json(tmp_path):
    """守护 PLAN-00 D10：数据产物为 JSONL。此处仅确认 json 读写闭环可用。"""
    p = tmp_path / "metrics.jsonl"
    p.write_text(json.dumps({"pid": "p01", "faces": 100}) + "\n", encoding="utf-8")
    assert json.loads(p.read_text(encoding="utf-8").splitlines()[0])["faces"] == 100

# ---------------------------------------------------------------- JSONL 读取

NL = chr(10)          # 换行：用表达式而不是转义写法，避免被工具链二次转义


def test_iter_jsonl_reports_file_and_line(tmp_path):
    """坏行必须报 `文件名:行号`——这就是把 14 处手写解析收口到 common 的实际收益。"""
    bad = tmp_path / "x.jsonl"
    bad.write_text('{"key": "a"}' + NL + NL + '{"key": "b"}' + NL + '{"broken": ' + NL,
                   encoding="utf-8")

    # 前两行正常（用 islice 惰性取，别把生成器整个消费掉——坏行在第 4 行）
    assert [r["key"] for r in islice(iter_jsonl(bad), 2)] == ["a", "b"]
    with pytest.raises(ValueError, match="x.jsonl:4"):
        list(iter_jsonl(bad))


def test_read_jsonl_skips_blank_lines_and_missing_file(tmp_path):
    """空行跳过；文件不存在返回空表（"没有就跳过"是调用方常态，不抛异常）。"""
    f = tmp_path / "y.jsonl"
    f.write_text('{"key": "a"}' + NL + NL + '{"key": "b"}' + NL, encoding="utf-8")

    assert [r["key"] for r in read_jsonl(f)] == ["a", "b"]
    assert read_jsonl(tmp_path / "nope.jsonl") == []
    assert read_jsonl_by_key(tmp_path / "nope.jsonl") == {}


def test_read_jsonl_by_key_keeps_last(tmp_path):
    """同名键后者覆盖前者（与各产物"每键一行"的契约一致）。"""
    f = tmp_path / "z.jsonl"
    f.write_text('{"key": "a", "v": 1}' + NL + '{"key": "b"}' + NL
                 + '{"key": "a", "v": 2}' + NL, encoding="utf-8")

    got = read_jsonl_by_key(f)

    assert got["a"]["v"] == 2 and set(got) == {"a", "b"}
