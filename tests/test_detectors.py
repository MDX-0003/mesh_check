"""detectors：岛层检测器的守卫（PLAN-04 层 1）。

全部用合成网格（trimesh 盒体），覆盖：悬浮候选的检出与理由叙事、贴合不误报、
ε 语义（阈值内外两态）、群组岛、rank 与 parts.jsonl 口径对齐、表面采样的确定性
与贴面性、ε 平台区稳定性断言（42 模型实验结论的可执行版本）。
"""

import numpy as np
import pytest
import trimesh

from meshq.core.detectors import (CoLocatedDetector, FloatingIslandDetector, ModelContext,
                       _SurfIndex, bbox_gap_matrix, query_points,
                       surface_samples)

CFG = {"parts": {"island_eps_ratio": 0.03, "island_query_points": 500,
                 "island_surface_samples": 300,
                 "enable_a0": True, "require_bpy_agreement": True,
                 "coloc_overlap_tol": 0.001, "coloc_true": 0.6,
                 "coloc_partial": 0.2, "coloc_surface_samples": 500}}


def box(extents=(2.0, 2.0, 2.0), translate=(0.0, 0.0, 0.0)):
    m = trimesh.creation.box(extents=extents)
    m.apply_translation(translate)
    return m


def make_ctx(meshes, key="t@smart-topology", feature=None, tmp_path=None):
    """合成网格 → 写 GLB → ModelContext（rank 口径与生产一致：load → weld → split）。"""
    import trimesh as _t

    assert tmp_path is not None, "合成 GLB 必须落在 pytest tmp_path 下"
    glb = tmp_path / f"{key.replace('@', '_')}.glb"
    glb.parent.mkdir(parents=True, exist_ok=True)
    _t.util.concatenate(meshes).export(glb)
    return ModelContext(key, glb, feature=feature)


# ---------------------------------------------------------------- 表面采样

def test_surface_samples_deterministic_and_on_surface():
    m = box((2.0, 2.0, 2.0))
    s1 = surface_samples(m, 400)
    s2 = surface_samples(m, 400)
    assert s1.shape == (400, 3)
    assert np.array_equal(s1, s2)                       # 无 RNG，跨次一致
    assert _SurfIndex(m).min_distance(s1) < 1e-9        # 全部落在表面上
    assert len({tuple(p) for p in np.round(s1, 6)}) > 390  # 分散，不塌成点


def test_surface_samples_area_weighting_favors_big_faces():
    # 大面（2x2）+ 小面（0.1x0.1 长条）拼合：采样点应按面积比例落在大面上
    big = box((2.0, 2.0, 2.0))
    small = box((0.1, 0.1, 0.1))
    small.apply_translation((5.0, 0.0, 0.0))
    m = trimesh.util.concatenate([big, small])
    pts = surface_samples(m, 2000)
    near_big = np.sum(np.abs(pts[:, 0] - 5.0) > 0.5)    # 远离 small 位置
    assert near_big / 2000 > 0.98                       # 面积比约 400:1


def test_query_points_caps_vertices_and_appends_surface():
    m = trimesh.creation.icosphere(subdivisions=4)      # >5000 顶点
    pts = query_points(m, vertex_cap=500, surface_count=300)
    assert pts.shape == (800, 3)


def test_bbox_gap_matrix_lower_bound_semantics():
    a = box((2, 2, 2))
    b = box((0.5, 0.5, 0.5), translate=(2.0, 0, 0))     # 表面间隙 0.75
    c = box((0.5, 0.5, 0.5), translate=(0.5, 0, 0))     # 与 a 相交
    g = bbox_gap_matrix([a, b, c])
    assert g[0, 1] == pytest.approx(0.75)
    assert g[0, 2] == 0.0                               # 相交 → 0（下界语义）
    assert np.allclose(np.diag(g), 0.0)


def test_surf_index_min_distance_exact():
    a = box((2, 2, 2))
    b = box((0.5, 0.5, 0.5), translate=(2.75, 0, 0))    # 表面间隙 1.5
    assert _SurfIndex(a).min_distance(np.asarray(b.vertices, dtype=float)) \
        == pytest.approx(1.5)
    assert _SurfIndex(a).min_distance(surface_samples(b, 500)) \
        == pytest.approx(1.5)
    # 目标内部点到其所在组件表面的距离（k 候选覆盖全部 12 面 → 精确）
    assert _SurfIndex(a).min_distance(np.array([[0.0, 0.0, 0.0]])) \
        == pytest.approx(1.0)


# ---------------------------------------------------------------- 岛层判定

def test_single_component_model_no_findings(tmp_path):
    ctx = make_ctx([box()], tmp_path=tmp_path)
    rows = FloatingIslandDetector().detect(ctx, CFG)
    assert rows == []
    report = FloatingIslandDetector().island_report(ctx, CFG)
    assert report["n_islands"] == 1
    assert report["islands"][0]["is_main"] is True
    assert report["islands"][0]["gap_ratio"] is None


def test_detached_floater_flagged_as_floating(tmp_path):
    main = box((2.0, 2.0, 2.0))
    floater = box((0.4, 0.4, 0.4), translate=(0.0, 0.0, 1.5))  # 表面间隙 0.3
    ctx = make_ctx([main, floater], tmp_path=tmp_path)
    det = FloatingIslandDetector()
    rows = det.detect(ctx, CFG)
    assert len(rows) == 1
    r = rows[0]
    assert r["defect_class"] == "floating" and r["tier"] == "review"
    assert r["detector"] == "islands" and r["entry"] == "island"
    assert r["rank"] == 1                               # 面数同 12，稳定取原始序
    assert "与主体分离" in r["reason"] and "待人工复核" not in r["reason"]  # 事实串不含处置话术
    assert "自动删" not in r["reason"]                  # 只检出不删除
    report = det.island_report(ctx, CFG)
    assert report["n_islands"] == 2
    isl = next(i for i in report["islands"] if i["floating"])
    assert isl["ranks"] == [1] and isl["shards"] == 1
    assert isl["gap_ratio"] > report["eps_ratio"]
    assert abs(isl["gap_ratio"] - 0.3 / report["diag"]) < 1e-3   # 精确距离


def test_attached_component_not_flagged(tmp_path):
    """贴合（面接触、无共点顶点）不误报：接触距离 0 <= ε，同岛。"""
    main = box((2.0, 2.0, 2.0))
    leg = box((0.4, 0.4, 0.6), translate=(0.6, 0.6, 1.3))  # 底面贴在主盒顶面 z=1 上
    ctx = make_ctx([main, leg], tmp_path=tmp_path)
    det = FloatingIslandDetector()
    assert det.detect(ctx, CFG) == []
    report = det.island_report(ctx, CFG)
    assert report["n_islands"] == 1


def test_eps_semantics_inside_vs_outside(tmp_path):
    main = box((2.0, 2.0, 2.0))                                 # z ∈ [-1, 1]
    near = box((0.3, 0.3, 0.3), translate=(0.0, 0.0, 1.21))     # 顶面间隙 0.06
    far = box((0.3, 0.3, 0.3), translate=(1.41, 0.0, 0.0))      # 侧面间隙 0.26
    ctx = make_ctx([main, near, far], tmp_path=tmp_path)
    det = FloatingIslandDetector()
    rows = det.detect(ctx, CFG)
    # diag≈4.01，ε≈0.120：0.06 同岛、0.26 独立岛
    assert [r["rank"] for r in rows] == [2]
    report = det.island_report(ctx, CFG)
    near_island = next(i for i in report["islands"] if 1 in i["ranks"])
    assert near_island["is_main"] is True               # near 与主体同岛


def test_group_floaters_form_one_island(tmp_path):
    main = box((2.0, 2.0, 2.0))
    g1 = box((0.3, 0.3, 0.3), translate=(2.0, 0.0, 0.0))
    g2 = box((0.3, 0.3, 0.3), translate=(2.0, 0.35, 0.0))  # 与 g1 面贴合
    ctx = make_ctx([main, g1, g2], tmp_path=tmp_path)
    det = FloatingIslandDetector()
    rows = det.detect(ctx, CFG)
    assert {r["rank"] for r in rows} == {1, 2}
    assert all("岛内 2 件" in r["reason"] for r in rows)
    assert all(r["metrics"]["island_shards"] == 2 for r in rows)
    assert all(r["evidence"]["island_ranks"] == [1, 2] for r in rows)


def test_ranks_match_parts_jsonl_feature_rows(tmp_path):
    """特征行注入后：n_faces/siblings/diag_ratio 取特征行口径（与 parts.jsonl 同源）。"""
    main = box((2.0, 2.0, 2.0))
    floater = box((0.4, 0.4, 0.4), translate=(3.0, 0.0, 0.0))
    feature = {"main_rank": 0, "parts": [
        {"rank": 0, "n_faces": 12, "siblings": 0, "diag_ratio": 1.0},
        {"rank": 1, "n_faces": 12, "siblings": 0, "diag_ratio": 0.2},
    ]}
    ctx = make_ctx([main, floater], feature=feature, tmp_path=tmp_path)
    rows = FloatingIslandDetector().detect(ctx, CFG)
    assert len(rows) == 1
    assert rows[0]["rank"] == 1
    assert rows[0]["n_faces"] == 12
    assert rows[0]["metrics"]["siblings"] == 0
    assert rows[0]["metrics"]["diag_ratio"] == 0.2


def test_feature_main_rank_wins_over_geometry(tmp_path):
    main = box((2.0, 2.0, 2.0))
    other = box((1.0, 1.0, 1.0), translate=(6.0, 0.0, 0.0))    # 与主体远离
    ctx = make_ctx([main, other], tmp_path=tmp_path)
    assert ctx.main_rank == 0
    ctx.feature = {"main_rank": 1}                              # 特征行口径优先
    assert ctx.main_rank == 1


def test_eps_plateau_stable(tmp_path):
    """平台区稳定性（单测断言，PLAN-04 §四决策 3）：ε 在 2%~5% 全段内岛数不变。"""
    main = box((2.0, 2.0, 2.0))
    floater = box((0.4, 0.4, 0.4), translate=(0.0, 0.0, 1.5))
    ctx = make_ctx([main, floater], tmp_path=tmp_path)
    det = FloatingIslandDetector()
    counts = [det.island_report(ctx, CFG, eps_ratio=r)["n_islands"]
              for r in (0.02, 0.025, 0.03, 0.035, 0.04, 0.05)]
    assert counts == [2] * 6                            # 平台区内恒为 2 岛


def test_plateau_broken_outside_bracket(tmp_path):
    """平台区外的对照：ε 足够大时两岛合并（断言平台区断言本身有分辨力）。"""
    main = box((2.0, 2.0, 2.0))
    floater = box((0.4, 0.4, 0.4), translate=(0.0, 0.0, 1.5))
    ctx = make_ctx([main, floater], tmp_path=tmp_path)
    det = FloatingIslandDetector()
    assert det.island_report(ctx, CFG, eps_ratio=0.15)["n_islands"] == 1


def test_feature_none_and_empty_feature_are_fine(tmp_path):
    main = box((2.0, 2.0, 2.0))
    floater = box((0.4, 0.4, 0.4), translate=(3.0, 0.0, 0.0))
    ctx = make_ctx([main, floater], feature=None, tmp_path=tmp_path)
    rows = FloatingIslandDetector().detect(ctx, CFG)
    assert rows and rows[0]["metrics"]["siblings"] is None
    ctx2 = make_ctx([main, floater], feature={}, tmp_path=tmp_path)
    assert len(FloatingIslandDetector().detect(ctx2, CFG)) == 1


def patch(translate=(0.0, 0.0, 1.0), half=0.4, cx=0.0, cy=0.0):
    """无厚度方片（2 三角面、手工顶点），默认贴在主盒顶面 z=1 上——真重叠的锚点几何。

    cx/cy 平移方片中心；与主盒顶面部分搭接时可构造 0.2~0.6 的灰区重叠率。
    """
    v = np.array([[-half, -half, 0.0], [half, -half, 0.0],
                  [half, half, 0.0], [-half, half, 0.0]], dtype=float)
    f = np.array([[0, 1, 2], [0, 2, 3]])
    m = trimesh.Trimesh(vertices=v, faces=f)
    m.apply_translation((cx + translate[0], cy + translate[1], translate[2]))
    return m


# ---------------------------------------------------------------- 共位检出

def co_feature(**part_over):
    """特征行骨架：本体 + 一个非本体件；用例只改关心的证据字段。"""
    return {"main_rank": 0, "parts": [
        {"rank": 0, "n_faces": 12, "is_main": True, "bbox_pinned": False,
         "engine_seen_by_bpy": True, "diag_ratio": 1.0},
        {"rank": 1, "n_faces": 2, "is_main": False, "bbox_pinned": False,
         "engine_seen_by_bpy": True, "diag_ratio": 0.01, "gap_ratio": 0.0,
         "vol_ratio": 1e-8, "bbox_thinness": 0.001, "contact_frac": 1.0,
         "siblings": 0, "nearest_rank": 0},
    ]}


def test_co_located_single_engine_evidence(tmp_path):
    """真重叠锚点：贴在主盒顶面的方片，表面 100% 与本体重合 → 重叠型 review。"""
    ctx = make_ctx([box(), patch()], feature=co_feature(), tmp_path=tmp_path)
    ctx.feature["parts"][1]["engine_seen_by_bpy"] = False
    rows = CoLocatedDetector().detect(ctx, CFG)
    assert len(rows) == 1
    r = rows[0]
    assert r["defect_class"] == "co_located" and r["tier"] == "review"
    assert r["entry"] == "A0" and r["rank"] == 1
    assert r["evidence"]["engine_seen_by_bpy"] is False
    assert r["evidence"]["bbox_pinned"] is False
    assert r["evidence"]["co_located_subtype"] == "overlap"
    assert r["metrics"]["overlap_frac"] > 0.9          # 方片整体贴在本体表面
    assert r["metrics"]["overlap_frac"] > 0.9 and r["tier"] == "review"
    assert "自动删" not in r["reason"]


def test_co_located_interface_subtype_not_a_defect(tmp_path):
    """界面贴合锚点：立方体叠立方体（重叠率 = 1/6 ≈ 0.167 < 0.2）→ 非缺陷 keep。"""
    main = box((2.0, 2.0, 2.0))
    leg = box((0.6, 0.6, 0.6), translate=(0.5, 0.5, 1.3))   # 底面贴合本体顶面
    feat = co_feature()
    feat["parts"][1].update({"n_faces": 12, "nearest_rank": 0,
                             "engine_seen_by_bpy": False})
    ctx = make_ctx([main, leg], feature=feat, tmp_path=tmp_path)
    rows = CoLocatedDetector().detect(ctx, CFG)
    assert len(rows) == 1
    r = rows[0]
    assert r["evidence"]["co_located_subtype"] == "interface"
    assert r["tier"] == "keep"                          # 非缺陷，不进复核队列
    assert 0.1 < r["metrics"]["overlap_frac"] < 0.25    # ≈ 底面/全面积 = 1/6
    assert r["tier"] == "keep"  # 非缺陷在展示层由 finding_reason_text 呈现


def test_co_located_partial_overlap_gray_zone(tmp_path):
    """灰区锚点：方片与本体顶面部分搭接（约 25% 面积重合）→ 0.2~0.6 灰区。"""
    main = box((2.0, 2.0, 2.0))
    # 0.8 宽方片，中心 x=1.2 → x ∈ [0.8, 1.6]，与主盒顶面（x ≤ 1.0）搭接 1/4 面积
    p = patch(cx=1.2, half=0.4)
    feat = co_feature()
    feat["parts"][1].update({"nearest_rank": 0, "engine_seen_by_bpy": False})
    ctx = make_ctx([main, p], feature=feat, tmp_path=tmp_path)
    rows = CoLocatedDetector().detect(ctx, CFG)
    assert len(rows) == 1
    r = rows[0]
    assert r["evidence"]["co_located_subtype"] == "partial"
    assert r["tier"] == "review"
    assert 0.2 <= r["metrics"]["overlap_frac"] < 0.6
    pass  # 灰区文案在展示层


def test_co_located_bbox_pinned_evidence(tmp_path):
    ctx = make_ctx([box(), patch()], feature=co_feature(), tmp_path=tmp_path)
    ctx.feature["parts"][1]["bbox_pinned"] = True
    ctx.feature["parts"][1]["engine_seen_by_bpy"] = False   # 双重证据
    rows = CoLocatedDetector().detect(ctx, CFG)
    assert len(rows) == 1
    assert rows[0]["evidence"]["bbox_pinned"] is True
    assert rows[0]["evidence"]["require_bpy_agreement"] is True
    assert rows[0]["evidence"]["co_located_subtype"] == "overlap"  # 贴顶面 → 真重叠


def test_co_located_healthy_part_not_flagged(tmp_path):
    ctx = make_ctx([box(), box((0.1, 0.1, 0.1))],
                   feature=co_feature(), tmp_path=tmp_path)
    assert CoLocatedDetector().detect(ctx, CFG) == []


def test_co_located_respects_config_gates(tmp_path):
    mesh = [box(), box((0.1, 0.1, 0.1))]
    off = dict(CFG, parts={**CFG["parts"], "enable_a0": False})
    ctx = make_ctx(mesh, feature=co_feature(), tmp_path=tmp_path)
    ctx.feature["parts"][1]["engine_seen_by_bpy"] = False
    assert CoLocatedDetector().detect(ctx, off) == []

    no_bpy = dict(CFG, parts={**CFG["parts"], "require_bpy_agreement": False})
    ctx2 = make_ctx(mesh, feature=co_feature(), tmp_path=tmp_path)
    ctx2.feature["parts"][1]["engine_seen_by_bpy"] = False
    assert CoLocatedDetector().detect(ctx2, no_bpy) == []   # 门放宽后单引擎不触发


def test_co_located_main_never_flagged(tmp_path):
    feat = co_feature()
    feat["parts"][0]["bbox_pinned"] = True                  # 本体即便贴门也不检出
    ctx = make_ctx([box(), box((0.1, 0.1, 0.1))], feature=feat, tmp_path=tmp_path)
    assert CoLocatedDetector().detect(ctx, CFG) == []


def test_registry_covers_both_classes(tmp_path):
    from meshq.core.findings import CLASS_CO_LOCATED, CLASS_FLOATING
    assert {type(d) for d in __import__("meshq.core.detectors", fromlist=["REGISTRY"]).REGISTRY} == {
        CoLocatedDetector, FloatingIslandDetector}
    feats = {"co_located": CoLocatedDetector, "floating": FloatingIslandDetector}
    for cls_name, cls in feats.items():
        assert cls.defect_class == cls_name
