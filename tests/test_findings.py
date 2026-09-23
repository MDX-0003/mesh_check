"""findings：检出结果契约的守卫（PLAN-04 §三）。

覆盖三件事：make_finding 的字段校验（尤其 tier="auto" 拒绝写入——只检出不删除的拍板
要在数据结构层面锁死）、多检测器合并的 (key, rank) 优先级、JSONL 往返与汇总口径。
"""

import pytest

from meshq.core.findings import (CLASS_CO_LOCATED, CLASS_FLOATING, CLASS_NONE, CLASS_OTHER,
                      TIER_AUTO_RESERVED, TIER_KEEP, TIER_REVIEW, count_by_class,
                      make_finding, merge, read_findings, summarize,
                      write_findings)


def row(**over):
    base = dict(key="pX@smart-topology", detector="co_located",
                defect_class=CLASS_CO_LOCATED, rank=3, reason="证据：仅单引擎可见")
    base.update(over)
    return base


# ---------------------------------------------------------------- make_finding

def test_make_filling_defaults_and_normalization():
    f = make_finding(**row())
    assert f["tier"] == TIER_REVIEW
    assert f["entry"] is None and f["n_faces"] is None
    assert f["evidence"] == {} and f["metrics"] == {}
    assert isinstance(f["rank"], int)


def test_make_finding_rejects_bad_class_and_none():
    with pytest.raises(ValueError, match="defect_class 非法"):
        make_finding(**row(defect_class="broken"))
    with pytest.raises(ValueError, match="none 类是保留口径"):
        make_finding(**row(defect_class=CLASS_NONE))


def test_make_finding_rejects_auto_tier():
    """只检出不删除（PLAN-04 决策 1）：auto 是保留值，契约层直接拒绝。"""
    with pytest.raises(ValueError, match="保留值"):
        make_finding(**row(tier=TIER_AUTO_RESERVED))
    with pytest.raises(ValueError, match="tier 非法"):
        make_finding(**row(tier="delete"))


def test_make_finding_requires_core_fields():
    for missing in ("key", "detector", "defect_class", "reason"):
        fields = row()
        fields.pop(missing)
        with pytest.raises(ValueError, match="必填字段"):
            make_finding(**fields)
    with pytest.raises(ValueError, match="rank 非法"):
        make_finding(**row(rank=-1))


def test_keep_tier_is_writable():
    assert make_finding(**row(tier=TIER_KEEP))["tier"] == TIER_KEEP


# ---------------------------------------------------------------- merge

def test_merge_dedupes_by_key_rank_with_class_priority():
    a = make_finding(**row())                                            # co_located
    b = make_finding(**row(detector="islands", defect_class=CLASS_FLOATING))
    c = make_finding(**row(detector="triage", defect_class=CLASS_OTHER))
    other_model = make_finding(**row(key="pY@smart-topology",
                                     defect_class=CLASS_FLOATING))
    merged = merge([[c, a, b], [other_model, b]])
    # 同 (key, rank) 只留 co_located；另一模型不受影响；按 (key, rank) 排序
    assert [(m["key"], m["rank"], m["defect_class"]) for m in merged] == [
        ("pX@smart-topology", 3, CLASS_CO_LOCATED),
        ("pY@smart-topology", 3, CLASS_FLOATING),
    ]


def test_merge_keeps_first_when_priority_equal():
    a = make_finding(**row(reason="先到"))
    b = make_finding(**row(reason="后到"))
    assert len(merge([[a], [b]])) == 1
    assert merge([[a], [b]])[0]["reason"] == "先到"


# ---------------------------------------------------------------- 汇总口径

def test_count_by_class_includes_zero_entries():
    rows = [make_finding(**row()),
            make_finding(**row(rank=4, defect_class=CLASS_OTHER, detector="triage"))]
    assert count_by_class(rows) == {CLASS_FLOATING: 0, CLASS_CO_LOCATED: 1,
                                    CLASS_OTHER: 1}


def test_summarize_counts_findings_and_models():
    rows = [
        make_finding(**row()),
        make_finding(**row(rank=4)),
        make_finding(**row(key="pY@smart-topology", rank=1,
                           defect_class=CLASS_FLOATING, detector="islands")),
    ]
    s = summarize(rows)
    assert s[CLASS_CO_LOCATED] == {"findings": 2, "models": 1}
    assert s[CLASS_FLOATING] == {"findings": 1, "models": 1}
    assert s[CLASS_OTHER] == {"findings": 0, "models": 0}


# ---------------------------------------------------------------- JSONL 往返

def test_jsonl_roundtrip(tmp_path):
    rows = [make_finding(**row(evidence={"bbox_pinned": True}, metrics={"gap": 0.0})),
            make_finding(**row(key="pY@smart-topology", rank=7,
                               defect_class=CLASS_FLOATING, detector="islands",
                               entry="island", n_faces=12))]
    path = tmp_path / "findings.jsonl"
    write_findings(path, rows)
    assert read_findings(path) == rows


def test_read_findings_reports_bad_line(tmp_path):
    path = tmp_path / "findings.jsonl"
    good = make_finding(**row())
    path.write_text(
        "\n".join([__import__("json").dumps(good),
                   __import__("json").dumps({**good, "rank": 3, "tier": "auto"})]),
        encoding="utf-8")
    with pytest.raises(ValueError, match="检出行非法"):
        read_findings(path)


def test_read_findings_missing_file_returns_empty(tmp_path):
    assert read_findings(tmp_path / "nope.jsonl") == []
