"""review：裁决契约与落位规则的守卫（PLAN-05 §一/§二）。"""

import pytest

from meshq.core.review import (DISPOSITION_FAIL, DISPOSITION_LABEL, DISPOSITION_PASS,
                    DISPOSITION_PENDING, DISPOSITION_DIR, load_reviews,
                    make_record, plan_dispositions, upsert_review,
                    write_reviews)


def row(key="pX@smart-topology", tier="review"):
    return {"key": key, "tier": tier, "defect_class": "floating", "rank": 1}


# ---------------------------------------------------------------- make_record

def test_make_record_validates():
    assert make_record("k", "pass")["disposition"] == "pass"
    with pytest.raises(ValueError, match="disposition 非法"):
        make_record("k", "maybe")
    with pytest.raises(ValueError, match="key 缺失"):
        make_record("", "pass")
    assert make_record("k", "pass", reviewed_at="2026-09-22T00:00:00Z"
                       )["reviewed_at"] == "2026-09-22T00:00:00Z"  # 不自动改时间


def test_labels_and_dirs_cover_all_dispositions():
    for d in (DISPOSITION_PASS, DISPOSITION_FAIL, DISPOSITION_PENDING):
        assert d in DISPOSITION_LABEL and d in DISPOSITION_DIR


# ---------------------------------------------------------------- 读写

def test_upsert_and_load_roundtrip(tmp_path):
    path = tmp_path / "review.jsonl"
    upsert_review(path, "pA@smart-topology", DISPOSITION_FAIL, reason="整组位移")
    upsert_review(path, "pB@smart-topology", DISPOSITION_PASS)
    upsert_review(path, "pA@smart-topology", DISPOSITION_PENDING, reason="改判")
    recs = load_reviews(path)
    # 每 key 取最新；其他模型的记录保留
    assert recs["pA@smart-topology"]["disposition"] == DISPOSITION_PENDING
    assert recs["pA@smart-topology"]["reason"] == "改判"
    assert recs["pB@smart-topology"]["disposition"] == DISPOSITION_PASS


def test_write_is_atomic_no_tmp_leftover(tmp_path):
    path = tmp_path / "review.jsonl"
    write_reviews(path, {"k": make_record("k", "pass")})
    assert list(tmp_path.glob("*.tmp")) == []
    assert load_reviews(path)["k"]["disposition"] == "pass"


def test_load_missing_file_returns_empty(tmp_path):
    assert load_reviews(tmp_path / "nope.jsonl") == {}


def test_load_rejects_bad_record(tmp_path):
    path = tmp_path / "review.jsonl"
    path.write_text('{"key": "k", "disposition": "maybe"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="裁决记录非法"):
        load_reviews(path)


# ---------------------------------------------------------------- 落位规则

def test_plan_dispositions_rule_branches():
    rows = [row("pClean", tier="keep"),            # 界面贴合 keep：不影响落位
            row("pReview", tier="review"),
            row("pReview", tier="review")]
    plan = plan_dispositions(rows, {}, ["pClean", "pReview", "pHollow"])
    assert plan["pClean"]["disposition"] == DISPOSITION_PASS    # 只有 keep 行
    assert plan["pClean"]["review_count"] == 0
    assert plan["pReview"]["disposition"] == DISPOSITION_PENDING
    assert plan["pReview"]["review_count"] == 2
    assert plan["pHollow"]["disposition"] == DISPOSITION_PASS   # 无任何检出


def test_plan_dispositions_human_wins_and_fail_only_from_human():
    rows = [row("pA"), row("pB")]
    reviews = {"pA": make_record("pA", DISPOSITION_FAIL, reason="人工判废"),
               "pB": make_record("pB", DISPOSITION_PASS)}
    plan = plan_dispositions(rows, reviews, ["pA", "pB"])
    assert plan["pA"]["disposition"] == DISPOSITION_FAIL        # fail 只来自人工
    assert plan["pA"]["source"] == "human"
    assert plan["pB"]["disposition"] == DISPOSITION_PASS
    assert plan["pB"]["source"] == "human"                      # 人工通过也不被规则覆盖
