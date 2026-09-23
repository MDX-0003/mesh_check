"""review_serve：审核台的守卫（PLAN-05 §三）。

真实起 ThreadingHTTPServer（随机端口）+ urllib 调用，覆盖：
静态服务与目录穿越防护、裁决 POST 的完整闭环（状态源落盘 → 文件夹移动 →
模型页/队列页重渲染）、非法请求拒绝。
"""

import json
import threading
import urllib.request
from pathlib import Path

import pytest

import test_deliver as td

DATE = td.DATE


@pytest.fixture()
def server(tmp_path):
    src = td.make_world(tmp_path)
    rc, _ = td.run_deliver(tmp_path, src)
    assert rc == 0
    from meshq.tools import review_server as review_serve
    handler = review_serve.make_handler(tmp_path / "results" / DATE,
                                        tmp_path / "data")
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", tmp_path
    srv.shutdown()
    srv.server_close()


def post(base, payload):
    req = urllib.request.Request(
        base + "/api/disposition", method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


def test_serves_index_and_blocks_traversal(server):
    base, tmp = server
    html = urllib.request.urlopen(base + "/").read().decode("utf-8")
    assert "交付队列" in html
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(base + "/../../pyproject.toml")
    assert e.value.code in (403, 404)


def test_disposition_post_full_loop(server):
    base, tmp = server
    resp = post(base, {"key": "pClean@smart-topology",
                       "disposition": "fail", "reason": "复核推翻"})
    assert resp["ok"] is True and resp["disposition"] == "fail"
    # 状态源落盘（人工裁决永不覆盖）
    from meshq.core.review import load_reviews
    recs = load_reviews(tmp / "data" / "review.jsonl")
    assert recs["pClean@smart-topology"]["disposition"] == "fail"
    # 文件夹移动：passed → failed
    root = tmp / "results" / DATE
    assert not (root / "passed" / "pClean@smart-topology").exists()
    moved = root / "failed" / "pClean@smart-topology"
    assert moved.is_dir()
    # 模型页重渲染：标题与裁决更新
    page = (moved / "pClean@smart-topology.html").read_text(encoding="utf-8")
    assert "确定不通过" in page and "复核推翻" in page
    # 队列页重渲染：类别计数更新
    index = (root / "index.html").read_text(encoding="utf-8")
    assert "确定通过（0）" in index and "确定不通过（2）" in index


def test_post_rejects_bad_disposition(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base, {"key": "pClean@smart-topology", "disposition": "maybe"})
    assert e.value.code == 400


def test_rejudgement_moves_back_and_forth_without_leftovers(server):
    """改判往返 pass → fail → pass：三类别里只留一份、每步计数正确。"""
    base, tmp = server
    root = tmp / "results" / DATE
    key = "pClean@smart-topology"          # 规则落位 = 确定通过（无待复核检出）

    post(base, {"key": key, "disposition": "fail", "reason": "第一次改判"})
    assert (root / "failed" / key).is_dir() and not (root / "passed" / key).exists()

    post(base, {"key": key, "disposition": "pass", "reason": "推翻上一次"})
    assert (root / "passed" / key).is_dir()
    hits = [d for d in ("passed", "pending", "failed") if (root / d / key).exists()]
    assert hits == ["passed"], f"该模型不应在多个类别目录里留下副本：{hits}"
    index = (root / "index.html").read_text(encoding="utf-8")
    assert "确定通过（1）" in index and "确定不通过（1）" in index


def test_human_pending_moves_rule_passed_model(server):
    """规则判"通过"的模型被人工判为"待定"后，必须真的挪进 pending 并计入待裁。"""
    base, tmp = server
    root = tmp / "results" / DATE
    key = "pClean@smart-topology"
    assert (root / "passed" / key).is_dir()

    post(base, {"key": key, "disposition": "pending"})

    assert (root / "pending" / key).is_dir() and not (root / "passed" / key).exists()
    assert "待人工裁决（1）" in (root / "index.html").read_text(encoding="utf-8")
    page = (root / "pending" / key / f"{key}.html").read_text(encoding="utf-8")
    assert "待人工裁决" in page


def test_repeated_same_verdict_keeps_state_source_single_line(server):
    """重复提交同一条裁决：状态源按 key 合并，不留重复行（upsert 语义）。"""
    base, tmp = server
    key = "pClean@smart-topology"
    for _ in range(3):
        post(base, {"key": key, "disposition": "pass", "reason": "同一条"})

    lines = [l for l in (tmp / "data" / "review.jsonl")
             .read_text(encoding="utf-8").splitlines() if l.strip()]
    # 同一 key 只留一条（其余模型的记录保留不动——夹具里 pDirty 那条来自 make_world）
    for key2 in ("pClean@smart-topology", "pDirty@smart-topology"):
        rows = [l for l in lines if json.loads(l)["key"] == key2]
        assert len(rows) == 1, f"{key2} 在状态源里出现 {len(rows)} 条"
    from meshq.core.review import load_reviews
    assert load_reviews(tmp / "data" / "review.jsonl")["pClean@smart-topology"][
        "disposition"] == "pass"


def test_empty_reason_verdict_keeps_card_text(server):
    """空理由裁决（审核台不填理由时的默认提交）：卡片副标题不能变成空行。"""
    base, tmp = server
    post(base, {"key": "pDirty@smart-topology", "disposition": "pending", "reason": ""})

    index = (tmp / "results" / DATE / "index.html").read_text(encoding="utf-8")
    cards = [l for l in index.splitlines() if 'class="meta"' in l]
    assert any("悬浮 1 条" in l for l in cards), "空理由时应回退到问题构成"


def test_bad_requests_never_touch_state_source(server):
    """缺 key / 坏 JSON / 非法 disposition 一律 400，且不写出状态源。"""
    base, tmp = server
    state = tmp / "data" / "review.jsonl"
    before = state.read_text(encoding="utf-8")      # 夹具里已有 pDirty 的一条裁决

    for payload in ({"key": "", "disposition": "pass"},
                    {"disposition": "pass"},
                    {"key": "pClean@smart-topology", "disposition": "maybe"}):
        with pytest.raises(urllib.error.HTTPError) as e:
            post(base, payload)
        assert e.value.code == 400

    req = urllib.request.Request(base + "/api/disposition", method="POST",
                                 data=b"{not json}",
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e2:
        urllib.request.urlopen(req)
    assert e2.value.code == 400

    assert state.read_text(encoding="utf-8") == before, "非法请求不应改动状态源"


def test_stale_review_mirror_is_removed_when_state_source_empty(tmp_path):
    """清空裁决后重建交付：根目录那份过期的 review.jsonl 镜像必须被删掉。"""
    src = td.make_world(tmp_path)
    rc, _ = td.run_deliver(tmp_path, src)
    assert rc == 0
    mirror = tmp_path / "results" / DATE / "review.jsonl"
    assert mirror.is_file()                       # 有裁决 → 有镜像

    (src / "review.jsonl").unlink()               # 状态源被清空（复位场景）
    rc, _ = td.run_deliver(tmp_path, src)

    assert rc == 0
    assert not mirror.exists(), "状态源为空时不应残留过期镜像"


def test_unknown_key_is_rejected_without_side_effects(server):
    """未知 key（笔误/别的批次）必须拒绝：不写状态源、不在队列页长出幽灵卡片。

    2026-09-23 实测：修前返回 200、把记录写进状态源，并让队列页多出一张没有模型目录的卡片
    （20 → 21 张）。UI 点不出来，但直接调 API 会中招。
    """
    base, tmp = server
    root = tmp / "results" / DATE
    state = tmp / "data" / "review.jsonl"
    before_state = state.read_text(encoding="utf-8") if state.is_file() else None
    before_cards = (root / "index.html").read_text(encoding="utf-8").count('class="card row"')

    with pytest.raises(urllib.error.HTTPError) as e:
        post(base, {"key": "typo@smart-topology", "disposition": "pass"})

    assert e.value.code == 400
    after_state = state.read_text(encoding="utf-8") if state.is_file() else None
    assert after_state == before_state, "未知 key 不应写状态源"
    index = (root / "index.html").read_text(encoding="utf-8")
    assert "typo@smart-topology" not in index
    assert index.count('class="card row"') == before_cards, "不应新增卡片"
