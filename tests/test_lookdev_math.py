"""lookdev_math 单测：色彩空间、灯位数学、视图表、图例记录。

这些算式原先与 `import bpy` 同处 render_one.py，因而零覆盖；本档是它们的第一层安全网。
"""

from pathlib import Path

import pytest

from meshq.core.lookdev_math import (LOOKDEV_DEFAULTS, VIEW_GRID, VIEWS, area_light_position,
                          hex_to_linear, hex_to_rgb, srgb_to_linear)

SRC = Path(__file__).resolve().parent.parent / "meshq" / "core" / "lookdev_math.py"


# ---------------------------------------------------------------- 模块守卫

def test_module_is_bpy_free():
    """本模块必须能在无 Blender 环境 import；出现 bpy 相关 import 即违反边界（PLAN-03.1 §3）。"""
    for line in SRC.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith(("import ", "from ")):
            assert "bpy" not in s, f"lookdev_math 不得依赖 bpy：{s}"


# ---------------------------------------------------------------- 色彩空间

def test_srgb_to_linear_endpoints():
    assert srgb_to_linear(0.0) == 0.0
    assert srgb_to_linear(1.0) == pytest.approx(1.0)


def test_srgb_to_linear_piecewise_is_continuous():
    """0.04045 是分段点；两侧极限应当接近（防止有人误改阈值或幂次）。"""
    lo = srgb_to_linear(0.04045)          # 线性段
    hi = srgb_to_linear(0.04046)          # 幂函数段
    assert abs(hi - lo) < 1e-5


def test_srgb_to_linear_known_midgray():
    """sRGB 128/255 ≈ 0.50196 → 线性 ≈ 0.2159（教科书锚点，独立于实现）。"""
    assert srgb_to_linear(128 / 255.0) == pytest.approx(0.2159, abs=2e-3)


def test_srgb_to_linear_is_monotonic():
    vals = [srgb_to_linear(i / 100) for i in range(101)]
    assert all(b >= a for a, b in zip(vals, vals[1:]))


def test_hex_to_linear_endpoints_and_dark():
    assert hex_to_linear("#FFFFFF") == pytest.approx((1.0, 1.0, 1.0))
    assert hex_to_linear("#000000") == (0.0, 0.0, 0.0)
    # 线壳色 #141417 极暗：线性值必须远小于 sRGB 归一（0.08 → ~0.007）
    assert all(c < 0.02 for c in hex_to_linear("#141417"))


def test_hex_to_linear_matches_srgb_of_hex_to_rgb():
    h = "#B8D1FF"
    assert hex_to_linear(h) == pytest.approx(
        tuple(srgb_to_linear(c / 255.0) for c in hex_to_rgb(h)))


# ---------------------------------------------------------------- 灯位与视图

def test_area_light_position_axes():
    """方位角 0 = 正前（-Y）；方位角 90 = 画幅右侧（+X）；仰角 90 = 正上方（+Z）。"""
    x, y, z = area_light_position(0.0, 0.0, 3.2)
    assert x == pytest.approx(0.0, abs=1e-12)
    assert y == pytest.approx(-3.2)
    assert z == pytest.approx(0.0, abs=1e-12)

    x, y, z = area_light_position(90.0, 0.0, 3.2)
    assert x == pytest.approx(3.2) and y == pytest.approx(0.0, abs=1e-12)

    x, y, z = area_light_position(0.0, 90.0, 3.2)
    assert z == pytest.approx(3.2) and y == pytest.approx(0.0, abs=1e-12)


def test_area_light_position_preserves_distance():
    """只旋转不缩放：任意方位/仰角下到原点的距离恒等于 dist。"""
    for az, el in ((0, 0), (-40, 35), (150, 40), (-135, 12), (180, -30)):
        p = area_light_position(float(az), float(el), 3.2)
        assert sum(v * v for v in p) ** 0.5 == pytest.approx(3.2)


def test_area_light_position_negative_azimuth_goes_left():
    """负方位角落在画幅左侧（x<0）——与 config 注释口径一致。"""
    assert area_light_position(-40.0, 35.0, 3.2)[0] < 0


def test_views_table_semantics():
    assert VIEW_GRID == ("front", "top", "right")
    assert set(VIEWS) == {"front", "top", "right", "iso"}
    fx, fy, fz = VIEWS["front"]
    assert fx == 0 and fy < 0                       # 正面：正前 -Y
    assert VIEWS["right"][0] > 0                    # 侧面：+X
    assert VIEWS["top"][2] == max(v[2] for v in VIEWS.values())   # 顶视图最高


def test_lookdev_defaults_cover_required_keys():
    """编排器传入的 lookdev 会 update 到这些缺省上；缺键会让渲染在 bpy 侧才炸。"""
    required = {"background_mode", "bg_gradient_top", "bg_gradient_bottom",
                "bg_solid_color", "key_energy", "key_color", "key_azimuth",
                "key_elevation", "rim_energy", "rim_color", "fill_energy",
                "ground_plane", "view_transform", "highlight_view_transform",
                "emission_strength", "wire_thickness", "wire_color",
                "wire_display_faces", "wire_ssaa"}
    assert required <= set(LOOKDEV_DEFAULTS)


# ---------------------------------------------------------------- 图例记录

def _pieces():
    return [
        {"n_faces": 816, "n_verts": 420, "diag": 2.3879823, "volume": 1.5,
         "is_small": False},
        {"n_faces": 728, "n_verts": 380, "diag": 0.9142, "volume": 0.2,
         "is_small": False},
        {"n_faces": 2, "n_verts": 4, "diag": 0.0057, "volume": 0.0,
         "is_small": True},
    ]


def test_hex_to_rgb_parses_and_ignores_hash():
    """hex_to_rgb 随"每件一色"撤销时从 palette 迁入 lookdev_math（颜色入口，不能跟着删）。"""
    assert hex_to_rgb("#4E79A7") == (78, 121, 167)
    assert hex_to_rgb("4E79A7") == (78, 121, 167)
    assert hex_to_rgb("#FFFFFF") == (255, 255, 255)


def test_hex_to_linear_is_built_on_hex_to_rgb():
    r, g, b = hex_to_linear("#808080")
    assert abs(r - g) < 1e-12 and abs(g - b) < 1e-12
    assert 0.2 < r < 0.25          # sRGB 0.5 → 线性 ≈ 0.214
