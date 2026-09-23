import pytest

from meshq.core.geometry import (bbox_stats, bounds_stats, classify_edge_counts,
                          classify_pieces, compute_verdict, main_piece_index)


def test_bbox_stats_unit_cube():
    coords = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 1)]
    s = bbox_stats(coords)
    assert s["volume"] == pytest.approx(1.0)
    assert s["diag"] == pytest.approx(3 ** 0.5)


def test_bbox_stats_empty():
    assert bbox_stats([]) == {"diag": 0.0, "volume": 0.0}


def _pieces(*diags):
    return [{"diag": d, "n_faces": 10} for d in diags]


def test_classify_marks_only_below_ratio():
    pieces = _pieces(1.0, 0.05, 0.5)
    classify_pieces(pieces, diag_ratio=0.10)
    assert [p["is_small"] for p in pieces] == [False, True, False]
    assert pieces[2]["diag_ratio_to_main"] == pytest.approx(0.5)


def test_classify_pair_of_shoes_not_flagged():
    """成对等大部件（一双鞋）diag 比接近 1，不得误标为碎片。"""
    pieces = _pieces(1.0, 0.92)
    classify_pieces(pieces, diag_ratio=0.10)
    assert all(not p["is_small"] for p in pieces)


def test_classify_single_piece():
    pieces = _pieces(1.0)
    classify_pieces(pieces, 0.10)
    assert pieces[0]["is_small"] is False


def test_classify_empty_and_zero_main():
    classify_pieces([], 0.1)
    pieces = _pieces(0.0, 0.0)
    classify_pieces(pieces, 0.1)
    assert all(not p["is_small"] for p in pieces)


def test_verdict_three_tiers():
    kw = dict(frag_reject_ratio=0.05)
    assert compute_verdict(n_pieces=1, n_small_pieces=0,
                           fragment_face_ratio=0.0, non_manifold_edges=0, **kw) == "直接入库"
    assert compute_verdict(n_pieces=3, n_small_pieces=2,
                           fragment_face_ratio=0.01, non_manifold_edges=0, **kw) == "修复后入库"
    assert compute_verdict(n_pieces=3, n_small_pieces=2,
                           fragment_face_ratio=0.20, non_manifold_edges=0, **kw) == "拒绝"
    # 无碎片但有非流形 → 修复后入库
    assert compute_verdict(n_pieces=1, n_small_pieces=0,
                           fragment_face_ratio=0.0, non_manifold_edges=4, **kw) == "修复后入库"


# ---------------------------------------------------------------- 边分类（PLAN-03 §3）

def test_classify_edge_counts_splits_boundary_and_non_manifold():
    assert classify_edge_counts([1, 1, 1, 1, 2, 2, 3]) == {
        "boundary_edges": 4, "non_manifold_edges": 1}


def test_classify_edge_counts_is_lossless_vs_legacy_metric():
    """新增两字段之和 == 历史口径 `计数 != 2`，口径变更不丢信息、可无损回退。"""
    counts = [1, 1, 2, 2, 2, 3, 4]
    ec = classify_edge_counts(counts)
    assert ec["boundary_edges"] + ec["non_manifold_edges"] == sum(
        1 for c in counts if c != 2)


def test_classify_edge_counts_empty():
    assert classify_edge_counts([]) == {"boundary_edges": 0, "non_manifold_edges": 0}


# ---------------------------------------------------------------- 主组件口径（PLAN-03 D5）

def test_main_piece_prefers_largest_bbox_not_most_faces():
    """实测：p01@smart-topology 面数主组件 816 面，而另两个部件的 bbox 是它的 1.29 倍。"""
    pieces = [{"diag": 0.766, "n_faces": 816},
              {"diag": 0.992, "n_faces": 599},
              {"diag": 0.694, "n_faces": 728}]
    assert main_piece_index(pieces) == 1


def test_main_piece_tie_breaks_by_faces_then_order():
    assert main_piece_index([{"diag": 1.0, "n_faces": 10},
                             {"diag": 1.0, "n_faces": 99}]) == 1
    assert main_piece_index([{"diag": 1.0, "n_faces": 10},
                             {"diag": 1.0, "n_faces": 10}]) == 0


def test_main_piece_empty():
    assert main_piece_index([]) == 0


def test_classify_uses_bbox_main_as_denominator():
    """分母必须是 bbox 最大的组件，否则相对阈值口径随“谁面数多”漂移。"""
    pieces = [{"diag": 0.8, "n_faces": 900}, {"diag": 1.6, "n_faces": 100}]
    classify_pieces(pieces, diag_ratio=0.10)
    assert pieces[1]["is_small"] is False                      # 主组件永不标记
    assert pieces[0]["diag_ratio_to_main"] == pytest.approx(0.5)
    assert pieces[0]["is_small"] is False


# ---------------------------------------------------------------- 规模口径唯一性

def test_bounds_stats_matches_bbox_stats():
    coords = [(0, 0, 0), (1, 0, 0), (0, 2, 0), (0, 0, 3)]
    s = bounds_stats((0, 0, 0), (1, 2, 3))
    assert s == bbox_stats(coords)
    assert s["diag"] == pytest.approx((1 + 4 + 9) ** 0.5)
    assert s["volume"] == pytest.approx(6.0)
