"""lookdev 展示层的纯逻辑：色彩空间转换、灯位数学、视图表、图例记录。

**不 import bpy / bmesh / mathutils** —— 本模块可在普通 Python 下 import，因而可单测。
这是它存在的理由：这批算式原与 `import bpy` 同处 render_one.py，导致零覆盖；
调灯光/相机/色彩参数时没有任何安全网。改本模块必改 tests/test_lookdev_math.py。
"""

from __future__ import annotations

from math import cos, radians, sin
from typing import Any


# 三视图（工程制图正面/顶面/侧面）+ iso 总览
VIEW_GRID = ("front", "top", "right")
VIEWS: dict[str, tuple[float, float, float]] = {
    "front": (0.0, -2.5, 0.65),
    "top": (0.0, -0.4, 2.45),
    "right": (2.5, 0.0, 0.65),
    "iso": (2.0, -2.0, 1.6),
}

# 单件隔离渲染（图证条带）的机位名——**唯一来源**：render_one 按此命名输出，
# 交付层按此取图。2026-09-23 前交付层用 `piece_<rank>_*.png` 通配，
# 把旧命名（front/top/right）的陈旧残留一起卷进条带，条带变成 6 张。
PIECE_VIEWS = ("face", "edge", "third")

# lookdev 展示场景参数（与 config.example.toml [render.lookdev] 一致的兜底缺省；
# 正式值由编排器经 argv JSON 传入，此处仅保证 render_one 可独立运行）
LOOKDEV_DEFAULTS: dict[str, Any] = {
    "background_mode": "gradient",
    "bg_gradient_top": "#B8BDC4",
    "bg_gradient_bottom": "#5A6068",
    "bg_solid_color": "#3C4048",
    "key_energy": 500.0,
    "key_color": "#FFE0B8",
    "key_azimuth": -40.0,
    "key_elevation": 35.0,
    "rim_energy": 350.0,
    "rim_color": "#B8D1FF",
    "fill_energy": 120.0,
    "ground_plane": True,
    "view_transform": "AgX",
    "highlight_view_transform": "Standard",
    "highlight_light_scale": 0.35,   # Standard 观变换下按比例减光，防灰体过曝（可读性修复 2026-09-21）
    "emission_strength": 2.0,
    "wire_thickness": 0.002,
    "wire_color": "#141417",
    "wire_display_faces": 20000,
    "wire_ssaa": 1,
    # 单件图证（明细页"图证"条带）的观感：与整机 lookdev 分开配置——
    # 它要回答的是"这一块组件是什么形状"，故不铺地面、纯色背景
    # （2026-09-23 用户口径），改色只需改 config 再跑 --target pieces。
    "pieces": {
        "background_color": "#FFFFFF",
        "ground_plane": False,
        "light_scale": 1.0,
        "wire_thickness_scale": 3.0,
    },
    # 逐检出定位图（PLAN-07）：半透明灰底 + 三色布尔拆分。视觉参数全部可调，
    # 改完只需 --target locator --force 重渲（不碰主通道）。
    "locator": {
        "background_color": "#FFFFFF",
        "ghost_color": "#6B7280",    # 灰底幽灵（白底上要够深才看得见轮廓）
        "ghost_alpha": 0.5,
        "light_scale": 0.35,         # 同单件图证：Standard 观变换下减光，防灰底与实体过曝
        "color_only_a": "#FF9E1A",   # 该组件独有的面
        "color_shared": "#E040A0",   # 两件共用的面（重叠的就是这一片）
        "color_only_b": "#2FB4C9",   # 邻居独有的面
        "emission_strength": 0.4,    # 自发光强度：过高会在 Standard 下过曝成白（实测 1.0 就白）
        "distance": 2.5,             # 眼位距离（归一化坐标系；标准视图用的量级）
        "fit": "a",                  # a=取景跟随组件本身（邻居常远大于它）；pair=两件；model=整机
        "margin": 1.8,               # pair 取景的边距倍率
        "zoom": 1.0,                 # 仅 fit=model 时有意义
        "offset_ratio": 0.002,       # A/B 沿法线的反向微偏（比共位 tol 大一个量级）
        "tol_ratio": 0.001,          # 三色拆分的判共位容差（= coloc_overlap_tol）
    },
}


def piece_lookdev(lookdev: dict[str, Any]) -> dict[str, Any]:
    """整机 lookdev + 单件图证覆盖 → 单件渲染用的 lookdev（纯函数，可单测）。

    只覆盖三件事：背景改为纯色、地面按配置开关、观变换沿用高亮通道的 Standard
    （平面件在 AgX 下会糊）。灯光能量与线壳粗细则由调用方按 light_scale /
    wire_thickness_scale 另行施加。
    """
    cfg = {**(lookdev.get("pieces") or {})}
    out = dict(lookdev)
    out["background_mode"] = "solid"
    out["bg_solid_color"] = str(cfg.get("background_color", "#FFFFFF"))
    out["ground_plane"] = bool(cfg.get("ground_plane", False))
    return out


def locator_config(lookdev: dict[str, Any]) -> dict[str, Any]:
    """定位图配置的兜底读取（缺段时用 LOOKDEV_DEFAULTS 的同名缺省）。"""
    base = dict(LOOKDEV_DEFAULTS["locator"])
    base.update(lookdev.get("locator") or {})
    return base


def locator_lookdev(lookdev: dict[str, Any]) -> dict[str, Any]:
    """整机 lookdev + 定位图覆盖 → 定位图场景用的 lookdev（纯函数，可单测）。

    只要两件事：纯色背景、不铺地面——灰底幽灵本身提供一个半透明的整机轮廓，
    地面只会添乱。
    """
    c = locator_config(lookdev)
    out = dict(lookdev)
    out["background_mode"] = "solid"
    out["bg_solid_color"] = str(c["background_color"])
    out["ground_plane"] = False
    return out


def piece_config(lookdev: dict[str, Any]) -> dict[str, Any]:
    """单件图证配置的兜底读取（缺段时用 LOOKDEV_DEFAULTS 的同名缺省）。"""
    base = dict(LOOKDEV_DEFAULTS["pieces"])
    base.update(lookdev.get("pieces") or {})
    return base


# ---------------------------------------------------------------- 色彩空间

def hex_to_rgb(h: str) -> tuple[int, int, int]:
    """#RRGGBB → 8bit RGB（不做线性转换；sRGB→线性由渲染端负责）。

    原先住在 palette.py，随"每件一色"（PLAN-07 §五）一并撤销时被迁到这里：它是
    `hex_to_linear` 的实现基础，也就是**所有通道的颜色入口**（背景色、线壳色、灯色
    都经它），跟配色分配毫无关系，不能跟着一起删。
    """
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def srgb_to_linear(c: float) -> float:
    """sRGB 分量（0..1）→ 线性空间分量。分段点 0.04045 与幂函数 2.4 为标准定义。"""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_linear(h: str) -> tuple[float, float, float]:
    """#RRGGBB（sRGB）→ 线性空间 RGB 三元组（渲染端用；画布底色用 palette.hex_to_rgb）。"""
    r, g, b = hex_to_rgb(h)
    return (srgb_to_linear(r / 255.0),
            srgb_to_linear(g / 255.0),
            srgb_to_linear(b / 255.0))


# ---------------------------------------------------------------- 灯位与视图

def area_light_position(az_deg: float, el_deg: float,
                        dist: float) -> tuple[float, float, float]:
    """AREA 灯球坐标摆位：方位角 0 = 正前（-Y），负值偏画幅左侧；仰角 0 = 地平。

    TRACK_TO 对准场景原点由调用方挂；本函数只管位置，故可离线单测。
    """
    az, el = radians(az_deg), radians(el_deg)
    return (cos(el) * sin(az) * dist, -cos(el) * cos(az) * dist, sin(el) * dist)


