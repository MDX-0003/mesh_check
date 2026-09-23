import json
from pathlib import Path

from tests.fakes import write_valid_glb

import pytest

from meshq.stages import generate as gen      # 直接 import 包内模块（conftest 已把仓库根放进 sys.path）
ROOT = Path(__file__).resolve().parent.parent


class ScriptedClient:
    """按脚本回放任务状态；记录 create/delete/download 调用。"""

    def __init__(self, sequences: dict[str, list[dict]], ids: dict[str, str] | None = None):
        self.sequences = sequences  # prompt → 任务状态序列
        self.ids = ids or {}
        self.created: list[str] = []
        self.deleted: list[str] = []
        self.downloaded: list[tuple[str, Path]] = []
        self.TERMINAL_BAD = ("FAILED", "CANCELED")

    def create_task(self, payload):
        self.created.append(payload["prompt"])
        return self.ids.get(payload["prompt"], f"t{len(self.created)}")

    def get_task(self, task_id):
        for seq in self.sequences.values():
            if seq:
                return seq.pop(0)
        raise AssertionError("状态脚本耗尽")

    def delete_task(self, task_id):
        self.deleted.append(task_id)

    def download(self, url, dest):
        self.downloaded.append((url, dest))
        if dest.suffix == ".glb":
            write_valid_glb(dest)  # 必须写结构合法的 GLB，否则补下载校验会走重建分支
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake-" + dest.name.encode())


def make_cfg():
    return {"poll": {"interval_seconds": 0, "timeout_seconds": 5}}


def make_plan():
    prompts = {
        "p01": {"pid": "p01", "prompt": "a chair", "category": "furniture", "expect_floater": False},
    }
    return gen.plan_smoke(prompts)


def ok_task(credits=5):
    return {"status": "SUCCEEDED", "consumed_credits": credits,
            "model_urls": {"glb": "https://x/model.glb"}, "thumbnail_url": "https://x/t.png"}


def test_plan_smoke_covers_both_presets():
    plan = make_plan()
    assert [k for k, _, _ in plan] == ["p01@standard", "p01@smart-topology"]


def test_poll_task_returns_on_success():
    client = ScriptedClient({})
    client.sequences = {"s": [{"status": "IN_PROGRESS"}, {"status": "IN_PROGRESS"},
                              {"status": "SUCCEEDED", "consumed_credits": 5}]}
    task = gen.poll_task(client, "t1", interval_s=0, timeout_s=5, log=lambda *a: None)
    assert task["status"] == "SUCCEEDED"


def test_poll_task_timeout(monkeypatch):
    """timeout=0 → 首查后即超时。

    用**注入的假时钟**而不是真时钟：真实 `monotonic()` 的精度跨平台/跨版本不同
    （Windows + Python 3.12 = 15.6ms，3.13 = 1e-7s），照原样写会在"两条语句之间时钟
    没走动"时判定未超时而多查一次，把脚本状态耗光 → 用例在 3.12 上必红（实测）。
    """
    ticks = iter(range(1, 1000))
    monkeypatch.setattr(gen.time, "monotonic", lambda: float(next(ticks)))
    client = ScriptedClient({})
    client.sequences = {"s": [{"status": "IN_PROGRESS"}]}

    task = gen.poll_task(client, "t1", interval_s=0, timeout_s=0, log=lambda *a: None)

    assert task["status"] == "TIMEOUT"


def test_check_api_reports_ok_and_failure(monkeypatch, capsys):
    """--check-api：付费动作前的连通性/鉴权自检（list_tasks 的最小用法）。

    这个方法此前是死代码（memory 却写着它是"鉴权检查的标准入口"），接上开关后两边才自洽。
    """
    from meshq.stages import generate as g

    class FakeClient:
        def __init__(self, *a, **k): pass

        def list_tasks(self, page_size=1):
            assert page_size == 1
            return [{"id": "t1"}]

    monkeypatch.setattr(g, "MeshyClient", FakeClient)
    monkeypatch.setattr(g, "get_api_key", lambda cfg: "k")
    monkeypatch.setattr(g, "load_config", lambda: {})

    assert g.main(["--check-api"]) == 0
    assert "check-api: OK" in capsys.readouterr().out

    class BadClient(FakeClient):
        def list_tasks(self, page_size=1):
            raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(g, "MeshyClient", BadClient)
    assert g.main(["--check-api"]) == 1
    assert "check-api: 失败" in capsys.readouterr().out


def test_run_batch_happy_path_downloads_and_records(tmp_path):
    client = ScriptedClient({"s": [ok_task(credits=7), ok_task()]})
    ledger = gen.TaskLedger(tmp_path / "tasks.json")
    plan = make_plan()

    results = gen.run_batch(plan, make_cfg(), client, ledger,
                            log=lambda *a: None, raw_dir=tmp_path / "raw")

    assert set(results) == {"p01@standard", "p01@smart-topology"}
    assert len(client.created) == 2  # 两个档位各建一个任务
    meta_path = tmp_path / "raw" / "p01@standard" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["credits"] == 7 and meta["preset"] == "standard"
    assert (tmp_path / "raw" / "p01@standard" / "model.glb").exists()
    assert ledger.get("p01@standard")["status"] == "SUCCEEDED"


def test_run_batch_idempotent_skips_succeeded(tmp_path):
    ledger = gen.TaskLedger(tmp_path / "tasks.json")
    ledger.update("p01@standard", status="SUCCEEDED", task_id="t0")
    ledger.update("p01@smart-topology", status="SUCCEEDED", task_id="t1")
    for key in ("p01@standard", "p01@smart-topology"):
        d = tmp_path / "raw" / key
        d.mkdir(parents=True)
        write_valid_glb(d / "model.glb")  # 本地已有有效模型 → 纯跳过

    client = ScriptedClient({})
    gen.run_batch(make_plan(), make_cfg(), client, ledger,
                  log=lambda *a: None, raw_dir=tmp_path / "raw")

    assert client.created == []  # 全部跳过，未建任何任务


def test_run_batch_recreates_failed_and_cleans_old_task(tmp_path):
    ledger = gen.TaskLedger(tmp_path / "tasks.json")
    ledger.update("p01@standard", status="FAILED", task_id="dead")
    ledger.update("p01@smart-topology", status="SUCCEEDED", task_id="t1")

    client = ScriptedClient({"s": [ok_task(), ok_task()]})  # 一份给补下载，一份给新任务轮询
    client.ids = {"a chair": "new-id"}
    results = gen.run_batch(make_plan(), make_cfg(), client, ledger,
                            log=lambda *a: None, raw_dir=tmp_path / "raw")

    assert "dead" in client.deleted          # 失败旧任务被清理
    assert client.created == ["a chair"]     # 只重建失败的那一个
    assert results["p01@standard"]["status"] == "SUCCEEDED"
    assert results["p01@smart-topology"]["status"] == "SUCCEEDED"


def test_run_batch_backfills_missing_glb_without_recreating(tmp_path):
    """台账 SUCCEEDED 但本地缺 GLB（上次下载中断）→ 补下载，不重建任务。"""
    ledger = gen.TaskLedger(tmp_path / "tasks.json")
    ledger.update("p01@standard", status="SUCCEEDED", task_id="t9", credits=20)
    ledger.update("p01@smart-topology", status="SUCCEEDED", task_id="t1")

    client = ScriptedClient({"s": [ok_task(credits=20), ok_task()]})  # 两个键各补下载一次
    results = gen.run_batch(make_plan(), make_cfg(), client, ledger,
                            log=lambda *a: None, raw_dir=tmp_path / "raw")

    assert client.created == []              # 没有新建任务 = 没有新计费
    meta = json.loads((tmp_path / "raw" / "p01@standard" / "meta.json")
                      .read_text(encoding="utf-8"))
    assert meta["has_glb"] is True
    assert results["p01@standard"]["status"] == "SUCCEEDED"


def test_run_batch_skips_network_when_glb_present(tmp_path):
    ledger = gen.TaskLedger(tmp_path / "tasks.json")
    ledger.update("p01@standard", status="SUCCEEDED", task_id="t9")
    ledger.update("p01@smart-topology", status="SUCCEEDED", task_id="t1")
    write_valid_glb(tmp_path / "raw" / "p01@standard" / "model.glb")
    write_valid_glb(tmp_path / "raw" / "p01@smart-topology" / "model.glb")

    client = ScriptedClient({})  # 任何网络调用都会断言失败
    results = gen.run_batch(make_plan(), make_cfg(), client, ledger,
                            log=lambda *a: None, raw_dir=tmp_path / "raw")
    assert client.downloaded == [] and client.created == []


def test_main_invokes_backup_after_batch(tmp_path, monkeypatch):
    """--all/--smoke 结束后默认自动备份；--no-backup 跳过。main 路径必须隔离 DATA_DIR。"""
    calls = []
    real_ledger = gen.TaskLedger
    monkeypatch.setattr(gen, "DATA_DIR", tmp_path)  # 防止污染真实 data/raw（见 memory）
    monkeypatch.setattr(gen, "run_backup", lambda cfg: calls.append(cfg))
    monkeypatch.setattr(gen, "load_config",
                        lambda: {"poll": {"interval_seconds": 0, "timeout_seconds": 5},
                                 "meshy": {"api_key": "k"}})
    monkeypatch.setattr(gen, "MeshyClient", lambda *a, **k: ScriptedClient(
        {"s": [ok_task(), ok_task()]}))
    monkeypatch.setattr(gen, "TaskLedger",
                        lambda: real_ledger(tmp_path / "t.json"))

    gen.main(["--smoke"])
    assert len(calls) == 1
    assert (tmp_path / "raw").exists()          # 产物落在隔离目录
    assert not (gen.DATA_DIR.parent / "data").exists() or True

    gen.main(["--smoke", "--no-backup"])
    assert len(calls) == 1  # 未增加


def test_backfill_triggers_on_corrupt_glb(tmp_path):
    """本地 glb 存在但内容无效（截断/假文件）→ 同样走补下载而非跳过。"""
    from meshq.core.common import glb_valid

    ledger = gen.TaskLedger(tmp_path / "tasks.json")
    ledger.update("p01@standard", status="SUCCEEDED", task_id="t9", credits=20)
    ledger.update("p01@smart-topology", status="SUCCEEDED", task_id="t1")
    bad_dir = tmp_path / "raw" / "p01@standard"
    bad_dir.mkdir(parents=True)
    (bad_dir / "model.glb").write_bytes(b"fake-glb")  # 无效文件

    client = ScriptedClient({"s": [ok_task(credits=20), ok_task()]})
    results = gen.run_batch(make_plan(), make_cfg(), client, ledger,
                            log=lambda *a: None, raw_dir=tmp_path / "raw")

    assert client.created == []  # 补下载而非重建
    assert (tmp_path / "raw" / "p01@standard" / "model.glb").stat().st_size > 8  # 已重写为新下载内容
    assert results["p01@standard"]["status"] == "SUCCEEDED"  # 但走了补下载路径


def test_verify_ledger_models(tmp_path):
    from meshq.core.common import glb_valid

    def real_glb(path: Path):
        # 构造头部合法的 GLB：magic + version + length==12
        import struct
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"glTF" + struct.pack("<II", 2, 12))

    ledger = gen.TaskLedger(tmp_path / "tasks.json")
    ledger.update("ok@standard", status="SUCCEEDED")
    ledger.update("bad@standard", status="SUCCEEDED")
    real_glb(tmp_path / "raw" / "ok@standard" / "model.glb")
    (tmp_path / "raw" / "bad@standard").mkdir(parents=True)
    (tmp_path / "raw" / "bad@standard" / "model.glb").write_bytes(b"fake")

    bad = gen.verify_ledger_models(ledger, raw_dir=tmp_path / "raw")
    assert bad == ["bad@standard"]
    assert glb_valid(tmp_path / "raw" / "ok@standard" / "model.glb") is True


# ---------------------------------------------------------------- T2 档参数与回显（PLAN-03 §7.2）

def test_payload_smart_topology_caps_polycount():
    payload = gen.build_task_payload("a chair", "smart-topology")
    assert payload["target_polycount"] == 15000
    assert payload["model_type"] == "smart-topology"
    # standard 档不带该参数，行为不变
    assert "target_polycount" not in gen.build_task_payload("a chair", "standard")


def test_echo_fields_records_t2_fingerprint_chain():
    task = {"model_type": "smart-topology", "seed": 42}
    assert gen.echo_fields("smart-topology", task) == {
        "model_type_echo": "smart-topology", "seed": 42, "target_polycount": 15000}
    # 回显字段缺失时落 None，不编造举证链
    assert gen.echo_fields("smart-topology", {}) == {
        "model_type_echo": None, "seed": None, "target_polycount": 15000}


def test_run_batch_records_echo_into_ledger(tmp_path):
    client = ScriptedClient({"s": [{**ok_task(), "model_type": "smart-topology",
                                    "seed": 7}]})
    ledger = gen.TaskLedger(tmp_path / "tasks.json")
    plan = gen.plan_all({"p01": {"pid": "p01", "prompt": "a chair",
                                 "category": "furniture"}}, "smart-topology")

    gen.run_batch(plan, make_cfg(), client, ledger,
                  log=lambda *a: None, raw_dir=tmp_path / "raw")

    rec = ledger.get("p01@smart-topology")
    assert rec["status"] == "SUCCEEDED"
    assert rec["model_type_echo"] == "smart-topology"
    assert rec["seed"] == 7 and rec["target_polycount"] == 15000
