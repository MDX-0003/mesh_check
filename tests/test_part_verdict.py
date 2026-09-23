"""part_verdict：判别级联的守卫。

这份测试就是 PLAN-03 §5.2 真值表的可执行版本——每一条入口各一例，外加两条"必须拦住"的：
A0 真实性门（拦住只在单引擎存在的贴片）与 A1 默认降级（未取证的入口不得动资产）。
"""

import pytest

from meshq.core.part_verdict import (TIER_FIX_ELIGIBLE, TIER_KEEP, TIER_REVIEW, decide,
                          has_same_size_peer, is_flimsy, is_solid, judge_model)


def make_cfg(**over):
    cfg = {
        "iso_ratio": 0.05,
        "diag_ratio_big": 0.30,
        "group_min_siblings": 2,
        "min_faces": 24,
        "solid_min_faces": 24,
        "volume_eps": 1e-06,
        "sibling_cluster_ratio": 1.25,
        "enable_a0": True,
        "require_bpy_agreement": True,
        "enable_a1": False,
    }
    cfg.update(over)
    return cfg


def part(**over):
    """一个"正常贴合实体大件"的基线组件，各用例只改它关心的字段。"""
    base = {"rank": 1, "is_main": False, "n_faces": 500, "vol_ratio": 0.01,
            "watertight": True, "euler": 2, "bbox_pinned": False,
            "engine_seen_by_bpy": True, "gap_ratio": 0.001, "siblings": 0,
            "diag_ratio": 0.90}
    base.update(over)
    return base


# ---------------------------------------------------------------- 谓词

def test_is_solid_requires_watertight_euler_and_faces():
    cfg = make_cfg()
    assert is_solid(part(), cfg) is True
    assert is_solid(part(watertight=False), cfg) is False
    assert is_solid(part(euler=1), cfg) is False
    assert is_solid(part(n_faces=6), cfg) is False


def test_is_flimsy_requires_all_three_conditions():
    cfg = make_cfg()
    flimsy = {"n_faces": 2, "vol_ratio": 0.0, "watertight": False}
    assert is_flimsy(flimsy, cfg) is True
    assert is_flimsy({**flimsy, "n_faces": 500}, cfg) is False      # 面数够
    assert is_flimsy({**flimsy, "vol_ratio": 0.02}, cfg) is False   # 有体积
    assert is_flimsy({**flimsy, "watertight": True}, cfg) is False  # 封闭
    assert is_flimsy({**flimsy, "vol_ratio": None}, cfg) is False   # 未知量不猜


def test_has_same_size_peer():
    assert has_same_size_peer({"siblings": 0}) is False
    assert has_same_size_peer({"siblings": 1}) is True
    assert has_same_size_peer({}) is False


# ---------------------------------------------------------------- 级联真值表

def test_main_piece_is_always_kept():
    d = decide(part(is_main=True, gap_ratio=9.9, n_faces=1), make_cfg())
    assert d["tier"] == TIER_KEEP and d["entry"] == "main"


def test_a0_blocks_bbox_pinned_piece():
    """§1.7：贴在模型包围盒平面上的退化片不得自动删。"""
    d = decide(part(n_faces=2, vol_ratio=0.0, watertight=False,
                    bbox_pinned=True, gap_ratio=0.6), make_cfg())
    assert d["tier"] == TIER_REVIEW and d["entry"] == "A0"
    assert "包围盒" in d["reason"]


def test_a0_blocks_piece_only_one_engine_sees():
    """存在性依赖焊接容差的候选不是高置信缺陷（PLAN-03 D12）。"""
    d = decide(part(engine_seen_by_bpy=False, gap_ratio=0.6), make_cfg())
    assert d["tier"] == TIER_REVIEW and d["entry"] == "A0"
    assert "焊接精度" in d["reason"]


def test_a0_can_be_disabled_for_comparison():
    """关掉 A0 后同一件会落到 A2/正常判定——用于对比"有门/无门"的差异。"""
    d = decide(part(engine_seen_by_bpy=False, gap_ratio=0.6), make_cfg(enable_a0=False))
    assert d["entry"] == "A2" and d["tier"] == TIER_FIX_ELIGIBLE


def test_a1_disabled_degrades_to_review():
    """未取证的入口不得动资产（PLAN-03 §5.3）。"""
    flimsy = dict(n_faces=2, vol_ratio=0.0, watertight=False)
    d = decide(part(**flimsy), make_cfg())
    assert d["tier"] == TIER_REVIEW and d["entry"] == "A1-off"
    assert "真实样本验证" in d["reason"]


def test_a1_enabled_became_fix_eligible():
    flimsy = dict(n_faces=2, vol_ratio=0.0, watertight=False)
    d = decide(part(**flimsy), make_cfg(enable_a1=True))
    assert d["tier"] == TIER_FIX_ELIGIBLE and d["entry"] == "A1"


def test_a2_far_and_isolated_is_auto_deleted():
    d = decide(part(gap_ratio=0.396, siblings=0), make_cfg())
    assert d["tier"] == TIER_FIX_ELIGIBLE and d["entry"] == "A2"
    assert "同尺寸组件" in d["reason"]


def test_c1_far_but_in_a_group_goes_to_review():
    """整组位移与孤立碎屑无法靠单轴区分 —— 保守交人判。"""
    d = decide(part(gap_ratio=0.396, siblings=3), make_cfg())
    assert d["tier"] == TIER_REVIEW and d["entry"] == "C1"


def test_b_touching_solid_large_is_kept():
    d = decide(part(gap_ratio=0.002, diag_ratio=0.91), make_cfg())
    assert d["tier"] == TIER_KEEP and d["entry"] == "B"


def test_b_does_not_require_watertight():
    """低模与半壳本来就不封闭：p01@smart 的三条腿（欧拉数 1/1/0）与 p06 的两块半壳都要判 B。

    用封闭性当"是部件"的门槛会系统性把真部件推进待审（PLAN-03 §5 的实测修正）。
    """
    d = decide(part(gap_ratio=0.006, diag_ratio=0.56, n_faces=354,
                    watertight=False, euler=1), make_cfg())
    assert d["tier"] == TIER_KEEP and d["entry"] == "B"
    assert "非封闭实体" in d["reason"]        # 形态信息仍在理由里可见


def test_b_grouped_equal_sized_pieces_are_kept():
    """p07@standard 实测：5 个各占 0.17 倍对角线、彼此等大的件是一个多部件物体的组成部分。

    只看"对主组件的比例"会把它们当碎屑；成群等大是有意拆分的形态特征（伪影是随机散落的）。
    """
    d = decide(part(gap_ratio=0.0055, diag_ratio=0.175, siblings=4, n_faces=90144), make_cfg())
    assert d["tier"] == TIER_KEEP and d["entry"] == "B"
    assert "4 件同尺寸同类" in d["reason"]


def test_c2_small_isolated_touching_piece_stays_in_review():
    """同一尺寸但孤立（无同类）→ 落待审，不进 B：成群与否是 B/C 的分界。"""
    d = decide(part(gap_ratio=0.0055, diag_ratio=0.175, siblings=0), make_cfg())
    assert d["tier"] == TIER_REVIEW and d["entry"] == "C2"


def test_b_single_large_piece_needs_no_peer():
    """单体大件天然孤立（椅背/桌面），成群不能是 B 的必要条件。"""
    d = decide(part(gap_ratio=0.002, diag_ratio=0.91, siblings=0), make_cfg())
    assert d["tier"] == TIER_KEEP and d["entry"] == "B"


def test_c2_solid_but_small_touching_goes_to_review():
    """p13 型：7,108 面封闭实体、贴合、孤立 —— 几何上与设计件不可分。"""
    d = decide(part(gap_ratio=0.0012, diag_ratio=0.085, n_faces=7108), make_cfg())
    assert d["tier"] == TIER_REVIEW and d["entry"] == "C2"
    assert "无法区分" in d["reason"]


def test_c2_non_solid_small_touching_goes_to_review():
    d = decide(part(gap_ratio=0.001, diag_ratio=0.08, watertight=False, euler=1),
               make_cfg())
    assert d["tier"] == TIER_REVIEW and d["entry"] == "C2"


def test_touching_but_not_big_enough_is_not_kept_as_part():
    """贴合实体但尺寸远小于主体 → 不能直接判为部件（尺寸轴参与）。"""
    d = decide(part(gap_ratio=0.001, diag_ratio=0.10), make_cfg())
    assert d["tier"] == TIER_REVIEW


def test_boundary_of_iso_threshold_is_inclusive():
    cfg = make_cfg(iso_ratio=0.05)
    assert decide(part(gap_ratio=0.05), cfg)["entry"] == "B"      # <= 阈值 → 贴合
    assert decide(part(gap_ratio=0.0500001, siblings=0,
                       diag_ratio=0.9), cfg)["entry"] == "A2"     # > 阈值且孤立


# ---------------------------------------------------------------- 模型级

def test_judge_model_rolls_up_counts_and_ranks():
    model = {
        "key": "pX@standard", "source": "model.glb", "n_pieces": 4,
        "bpy_n_pieces": 4, "engine_partition_match": 4,
        "parts": [
            part(rank=0, is_main=True),
            part(rank=1, gap_ratio=0.6, siblings=0),                   # A
            part(rank=2, gap_ratio=0.002, diag_ratio=0.91),            # B
            part(rank=3, gap_ratio=0.0012, diag_ratio=0.08, n_faces=7000),  # C
        ],
    }
    rec = judge_model(model, make_cfg())
    assert rec["counts"] == {TIER_FIX_ELIGIBLE: 1, TIER_KEEP: 2, TIER_REVIEW: 1}
    assert rec["auto_fix_ranks"] == [1]
    assert rec["review_ranks"] == [3]
    assert len(rec["decisions"]) == 4


def test_judge_model_empty_parts():
    rec = judge_model({"key": "pZ", "parts": []}, make_cfg())
    assert rec["counts"] == {TIER_FIX_ELIGIBLE: 0, TIER_KEEP: 0, TIER_REVIEW: 0}
    assert rec["auto_fix_ranks"] == []


# ---------------------------------------------------------------- v2 缺陷类映射（PLAN-04 §三）

def test_defect_class_mapping_all_entries():
    """级联入口 → 缺陷类：A0=共位、A2/C1=悬浮、A1-off/C2=非核心、B/main=none。"""
    cfg = make_cfg()
    cases = [
        (part(rank=1, bbox_pinned=True), "A0", "co_located"),              # 共位门
        (part(rank=1, gap_ratio=0.6, siblings=0), "A2", "floating"),       # 悬浮孤立
        (part(rank=1, gap_ratio=0.6, siblings=2), "C1", "floating"),       # 整组位移
        (part(rank=1, gap_ratio=0.002, diag_ratio=0.91), "B", "none"),     # 设计部件
        (part(rank=1, gap_ratio=0.0012, diag_ratio=0.08, n_faces=7000),
         "C2", "other"),                                                   # 几何不可分
        (part(rank=1, is_main=True), "main", "none"),                      # 本体
    ]
    for p, entry, defect_class in cases:
        d = decide(p, cfg)
        assert d["entry"] == entry, (p, d)
        model = {"key": "pX", "parts": [dict(p, is_main=False), p]}
        rec = judge_model(model, cfg)
        target = next(x for x in rec["decisions"] if x["rank"] == p["rank"])
        assert target["defect_class"] == defect_class, (entry, target)


def test_judge_model_defect_class_counts():
    model = {
        "key": "pX@standard", "parts": [
            part(rank=0, is_main=True),
            part(rank=1, bbox_pinned=True),                                # co_located
            part(rank=2, gap_ratio=0.002, diag_ratio=0.91),                # none (B)
            part(rank=3, gap_ratio=0.0012, diag_ratio=0.08, n_faces=7000), # other (C2)
        ],
    }
    rec = judge_model(model, make_cfg())
    assert rec["defect_class_counts"] == {"floating": 0, "co_located": 1,
                                          "other": 1, "none": 2}
