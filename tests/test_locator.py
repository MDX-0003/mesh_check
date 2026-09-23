"""locator：定位图视角选择的纯逻辑（PLAN-07 §3.3）。

覆盖：确定性方向候选、投影量（薄片正对 vs 侧看）、俯仰惩罚、标准视图优先条款。
"""

import math

import numpy as np

from meshq.core.locator import (_projected_bbox_short, canonical_directions, choose_view,
                     fibonacci_directions, project, score_direction)


def sliver():
    """一块躺在 XY 平面里的薄片：x∈[0,1]、y∈[0,0.5]、z=0（两三角面）。"""
    pts = np.array([[0, 0, 0], [1, 0, 0], [1, 0.5, 0], [0, 0.5, 0]], dtype=float)
    tris = np.array([pts[[0, 1, 2]], pts[[1, 2, 3]]], dtype=float)
    return tris, pts


def test_fibonacci_directions_deterministic_and_unit():
    a = fibonacci_directions(64)
    b = fibonacci_directions(64)
    assert len(a) == 64
    assert all(np.allclose(x, y) for x, y in zip(a, b))     # 无 RNG → 跨次一致
    assert all(abs(float(np.linalg.norm(d)) - 1.0) < 1e-9 for d in a)
    # 覆盖球面而不是挤在一处
    assert min(float(np.dot(a[i], a[j])) for i in range(0, 64, 7)
               for j in range(0, 64, 7) if i != j) < 0.5


def test_projection_face_on_versus_edge_on():
    """薄片正对（看 Z）短边最大、侧看（看 X）短边趋 0——短边项就是为这个存在的。"""
    _, pts = sliver()
    face_on = _projected_bbox_short(pts, np.array([0.0, 0.0, 1.0]))
    edge_on = _projected_bbox_short(pts, np.array([1.0, 0.0, 0.0]))
    assert abs(face_on - 0.5) < 1e-9
    assert edge_on < 1e-9
    assert face_on > edge_on


def test_project_is_isometric_on_a_known_plane():
    pts = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    out = project(pts, np.array([0.0, 0.0, 1.0]))
    assert np.allclose(np.linalg.norm(out[1] - out[0]), 1.0)


def test_canonical_directions_normalized():
    views = [("front", (0.0, -2.5, 0.65)), ("top", (0.0, -0.4, 2.45))]
    out = canonical_directions(views)
    assert [n for n, _ in out] == ["front", "top"]
    assert all(abs(float(np.linalg.norm(d)) - 1.0) < 1e-9 for _, d in out)


def test_choose_view_picks_face_on_for_a_flat_sliver():
    """核心行为：薄片必须选到"正对"的机位，而不是斜着一看是一条线。"""
    tris, pts = sliver()
    got = choose_view([tris], [pts], diag=math.sqrt(1 + 0.25),
                      canonical=canonical_directions(
                          [("front", (0.0, -2.5, 0.65)), ("top", (0.0, -0.4, 2.45)),
                           ("right", (2.5, 0.0, 0.65)), ("iso", (2.0, -2.0, 1.6))]))
    d = np.asarray(got["direction"], dtype=float)
    assert abs(float(d[2])) > 0.8, f"没选到正对方向：{d}（view={got['view']}）"


def test_elevation_penalty_favors_upward_views():
    _, pts = sliver()
    tris = np.array([pts[[0, 1, 2]]])
    below = score_direction(np.array([0.0, 0.0, -1.0]), [tris], [pts], 1.1)
    above = score_direction(np.array([0.0, 0.0, 1.0]), [tris], [pts], 1.1)
    assert below["pref"] < above["pref"]
    assert below["score"] < above["score"]          # 贴地/仰视被罚


def test_canonical_ratio_clause_prefers_standard_view():
    """标准视图优先条款：把比例设成 0 时必然选标准视图。"""
    tris, pts = sliver()
    canon = canonical_directions([("front", (0.0, -2.5, 0.65)),
                                  ("top", (0.0, -0.4, 2.45)),
                                  ("right", (2.5, 0.0, 0.65)),
                                  ("iso", (2.0, -2.0, 1.6))])
    forced = choose_view([tris], [pts], 1.1, canonical=canon,
                         cfg={"canonical_ratio": 0.0})
    assert forced["view"] in {"front", "top", "right", "iso"}
    # 而默认 0.9 时，薄片的正对优势明显，不该被标准视图顶掉
    auto = choose_view([tris], [pts], 1.1, canonical=canon)
    assert auto["view"] == "auto"


def test_choose_view_handles_degenerate_input():
    got = choose_view([], [], 0.0)
    assert got["direction"] and got["score"] == 0.0
