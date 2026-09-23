"""Blender 内执行的通道编排与产物契约（由 meshq/stages/render.py 经 blender.exe 调起）。

用法：
  blender.exe --background --python meshq/blender/render_one.py --
    <in.glb> <out_dir> <checks.json> <res_x> <res_y> <diag_ratio> [lookdev.json] [parts.json]

分工（PLAN-03.1 抽取结果 + 2026-09-23 目录化）：
  blender/bpy_geom.py    导入焊接、连通域、几何计数、归一化、像素投影（几何侧）
  blender/lookdev.py     世界/灯光/相机/材质/渲染（展示侧）
  ../lookdev_math.py     色彩空间、灯位数学、视图表、机位常量（纯函数，可单测，两侧共用）
  ../geometry.py         组件分类等纯逻辑（与 venv 侧 mesh_ops 同源口径）
  **本文件**             通道编排 + 对外产物契约（checks / 定位图 / 单件图证）

通道：
  base_*.png       EEVEE 平滑着色（三视图 front/top/right + iso 总览），AgX
  highlight_*.png  判别结果着色 + 线框叠加（三视图）：红 = A 档、琥珀 = C 档、
                   灰 = 保留，深色线 = 真实几何边；Standard 观变换 + 减光防过曝
  wire_*.png       W1 线壳：显示副本 + Wireframe 修改器深色几何边线（三视图，无投影影子）
  piece_*.png      逐检出单件隔离渲染（图证条带）
  locator_*.png    逐检出定位图（双机位：整机上下文 + 贴近组件）
产物 sidecar：
  checks.json（blender_checks.json）：bpy 原生几何指标 + 归一化 + 线框展示密度

parts.json（argv[7]，缺省 = 只渲染既有三通道）：
  {"channels": ["base","highlight","wire"], "piece_files": […], "locator_items_path": …}

红线（勿动）：线壳通道的 DECIMATE 必须在 WIREFRAME 之前——修改器按栈序求值，反过来会
让线壳直接吃满原始百万面网格（HANDOFF，p18 实测 >10min 无产出）。
附加：非三角面计数仅进 stdout 日志备查（全三角结论见 PLAN-02 §2.4），不改 checks 结构。
"""

from __future__ import annotations

import json
import os
import sys

import bpy

# 仓库根要在 path 上（`meshq` 是顶层包，Blender 内也按 `from meshq.core…` 绝对导入）。
# 本文件由 blender.exe --python <绝对路径> 直接执行，故 sys.path[0] 是本目录，必须自己补根。
_HERE = os.path.dirname(os.path.abspath(__file__))        # …/meshq/blender
_ROOT = os.path.dirname(os.path.dirname(_HERE))           # 仓库根（含 meshq/）
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from meshq.blender.bpy_geom import (WELD_THRESHOLD, ZERO_THRESHOLD, clear_scene,  # noqa: E402
                             count_degenerate, count_edge_classes,
                             count_self_intersect_faces, find_pieces,
                             import_and_weld, import_glb, normalize, sort_pieces)
from meshq.blender.lookdev import (assign_materials,  # noqa: E402
                             build_scene, make_materials, render_views, restore_lights,
                             scale_lights, set_engine, set_view_transform)
from meshq.core.geometry import classify_pieces  # noqa: E402
from meshq.core.lookdev_math import LOOKDEV_DEFAULTS, VIEW_GRID, hex_to_linear  # noqa: E402


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:]
    in_glb, out_dir, checks_path = argv[0], argv[1], argv[2]
    res_x, res_y = int(argv[3]), int(argv[4])
    diag_ratio = float(argv[5])
    lookdev = dict(LOOKDEV_DEFAULTS)
    if len(argv) > 6:
        lookdev.update(json.loads(argv[6]))
    # 通道选择与组件配色（PLAN-03 §6.1）。缺省 argv[7] = 只渲染既有三个通道
    parts = json.loads(argv[7]) if len(argv) > 7 else {}
    return (in_glb, out_dir, checks_path, res_x, res_y, diag_ratio, lookdev, parts)


# ---------------------------------------------------------------- 产物契约

def render_base_channel(scene, cams, obj, mats, n_faces, out_dir, lookdev) -> None:
    """通道一：EEVEE 平滑着色（三视图 + iso 总览），AgX 质感。"""
    gray = mats[0]
    set_view_transform(scene, lookdev["view_transform"])
    assign_materials(obj, [gray], [0] * n_faces)
    render_views(scene, cams, VIEW_GRID + ("iso",), "base_", out_dir)


# 判别档位 → 高亮材质槽：0 = 灰（B/主体），1 = 红（A 自动删候选），2 = 琥珀（C 待审）
TIER_SLOT = {"A": 1, "C": 2}


def render_highlight_channel(scene, cams, obj, mats, me, pieces, face_piece,
                             tiers: dict, res, out_dir, lookdev) -> None:
    """通道二：判别结果着色（三视图），Standard 观变换 + 按比例减光。

    着色按部件判别档位（PLAN-03 D10，tiers = bpy rank → "A"/"C"，由编排器从
    part_verdicts + parts 双引擎映射算出）：红 = A 档，琥珀 = C 档，灰 = 保留。
    全灰即"该模型无任何可疑组件"的证据。同时逐档位组件投影像素 bbox（v2：
    一组件一 bbox，供编排器裁逐组件放大拼图，不再做联合框）。
    """
    gray, red = mats[0], mats[1]
    amber, wire = mats[3], mats[2]
    res_x, res_y = res
    set_view_transform(scene, lookdev["highlight_view_transform"])
    saved_lights = scale_lights(scene, float(lookdev.get("highlight_light_scale", 1.0)))
    # 判别着色 + 线框叠加（可读性第三轮：面 = 档位色，壳 = 深色边，一图回答两个问题）。
    # 壳面材质号 = 原面材质号 + material_offset，故槽 3/4/5 全部给线壳深色。
    slots = (gray, red, amber, wire, wire, wire)
    face_slot = [TIER_SLOT.get(tiers.get(pid), 0) for pid in face_piece]
    assign_materials(obj, slots, face_slot)

    display = obj.copy()
    display.data = obj.data.copy()
    scene.collection.objects.link(display)
    display.data.materials.clear()
    for mat in slots:
        display.data.materials.append(mat)
    for poly, slot in zip(display.data.polygons, face_slot):
        poly.material_index = slot
    mod = display.modifiers.new("wire_overlay", "WIREFRAME")
    mod.thickness = float(lookdev["wire_thickness"])
    mod.use_replace = False
    mod.use_boundary = True
    mod.use_even_offset = False  # 同 wire 通道：even_offset 在退化面上会炸出巨型壳层
    mod.material_offset = 3
    obj.hide_render = True

    # highlight_regions.json（逐档位像素 bbox → crops/拼图）已随 PLAN-07 §五 撤销：
    # 定位改由逐检出定位图承担，交付层不再消费这条链。本通道本身保留，供报告对比图用。
    render_views(scene, cams, VIEW_GRID, "highlight_", out_dir)
    obj.hide_render = False
    bpy.data.objects.remove(display)
    restore_lights(saved_lights)


def render_wire_channel(scene, cams, obj, mats, total_faces, needs_decimate,
                        wire_faces, res, out_dir, lookdev) -> None:
    """通道三：W1 线壳（三视图）——显示副本 + Wireframe 修改器真几何边线（AgX 底）。

    超过 wire_display_faces 的网格用降采样副本（展示密度，非几何修改；原 GLB 不受影响）。
    """
    gray, wire = mats[0], mats[2]
    set_view_transform(scene, lookdev["view_transform"])
    res_x, res_y = res
    wire_ssaa = int(lookdev["wire_ssaa"])
    if wire_ssaa != 1:
        scene.render.resolution_x = res_x * wire_ssaa
        scene.render.resolution_y = res_y * wire_ssaa
    display = obj.copy()
    display.data = obj.data.copy()
    scene.collection.objects.link(display)
    # 栈序致命：必须先 DECIMATE 后 WIREFRAME（修改器按栈序求值）。
    # 反过来会让线壳直接吃满原始百万面网格（HANDOFF 红线，p18 实测 >10min 无产出）。
    if needs_decimate:
        dec = display.modifiers.new("decimate_display", "DECIMATE")
        dec.ratio = wire_faces / total_faces
    mod = display.modifiers.new("wire_shell", "WIREFRAME")
    mod.thickness = float(lookdev["wire_thickness"])
    mod.use_replace = False
    mod.use_boundary = True
    # even_offset 在世界空间逐面计算厚度偏移，遇到生成网格常见的退化/自交面会
    # 算出巨型壳层（实测 p02：最长边 9.74 = 7.2 倍对角线，渲染成"模型外的横线
    # /交叉线"）。关掉后按局部空间偏移，最长边回落到 0.09（可读性修复 2026-09-21）。
    mod.use_even_offset = False
    mod.material_offset = 1  # 线壳面 = 原面材质索引 0 + 1 → 深色槽
    display.data.materials.clear()
    display.data.materials.append(gray)
    display.data.materials.append(wire)
    obj.hide_render = True
    render_views(scene, cams, VIEW_GRID, "wire_", out_dir)
    obj.hide_render = False
    if wire_ssaa != 1:
        scene.render.resolution_x = res_x
        scene.render.resolution_y = res_y


def render_pieces_isolated(piece_files, res, lookdev, out_dir) -> None:
    """单件隔离渲染（PLAN-05 图证补齐）：每个组件单独导入、单独机位、三视图特写
    → `piece_<rank>_<view>.png`，view 取 `lookdev_math.PIECE_VIEWS`（唯一来源）。

    可读性要点（2026-09-22 审阅反馈"图证看不见内容"）：
    - 机位**贴合组件**：沿最薄扩展轴正对（薄片件能看到整个面），第二机位沿次薄轴
      （看侧缘轮廓），第三机位兜底——固定 front/top/right 对薄片区经常是侧对/出画；
    - **深色线壳叠加 + Standard 高对比**：平面件正面铺满画幅时，靠三角网格边线才
      能看出"这是一块面"；
    - 线壳加粗 3 倍：单件归一化后画幅被组件填满，主通道的 0.002 厚度远看不可见。

    场景与整机 lookdev **分开**（2026-09-23 用户口径）：**不铺地面、纯色背景**，
    因为这里要回答的是"这一块组件是什么形状"，地面只会干扰轮廓。背景色、地面开关、
    灯光与线壳倍率都在 `config.toml [render.lookdev.pieces]`，调完跑
    `--target pieces --keys <模型>` 即可只重渲图证。
    """
    import numpy as np
    import math

    from mathutils import Vector

    from meshq.blender.bpy_geom import clear_scene, import_and_weld, normalize
    from meshq.blender.lookdev import (assign_materials, build_scene, make_materials,
                         render_views, restore_lights, scale_lights,
                         set_view_transform)
    from meshq.core.lookdev_math import PIECE_VIEWS, piece_config, piece_lookdev

    pcfg = piece_config(lookdev)
    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y = res
    axes = [Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))]
    for pf in piece_files:
        clear_scene()
        obj = import_and_weld(pf)
        _, ground_z = normalize(obj)
        # 单件预览场景：纯色背景、不铺地面（只强调组件形状），机位另建
        build_scene(scene, piece_lookdev(lookdev), ground_z)
        mats = make_materials(lookdev)
        set_view_transform(scene, lookdev.get("highlight_view_transform", "Standard"))
        saved = scale_lights(scene, float(lookdev.get("highlight_light_scale", 1.0))
                             * float(pcfg.get("light_scale", 1.0)))
        gray, wire = mats[0], mats[2]
        slots = (gray, wire)
        assign_materials(obj, slots, [0] * len(obj.data.polygons))

        display = obj.copy()
        display.data = obj.data.copy()
        scene.collection.objects.link(display)
        display.data.materials.clear()
        for m in slots:
            display.data.materials.append(m)
        for poly in display.data.polygons:
            poly.material_index = 0
        mod = display.modifiers.new("wire_overlay", "WIREFRAME")
        mod.thickness = (float(lookdev["wire_thickness"])
                         * float(pcfg.get("wire_thickness_scale", 3.0)))
        mod.use_replace = False
        mod.use_boundary = True
        mod.use_even_offset = False
        mod.material_offset = 1
        obj.hide_render = True

        # 贴合机位：世界轴按组件扩展程度升序 → 正对最薄轴、侧视次薄轴。
        # 包围盒**从顶点直接算**，不用 display.bound_box：normalize() 是 me.transform()
        # 就地改网格，而 bound_box 要等 depsgraph 更新才跟上——刚 normalize 完就读它
        # 会拿到变换前的旧框，于是机位被放到旧位置、组件落在画外（2026-09-23 实测：
        # 单件图渲成纯背景空图，方差 0.3）。
        mw = display.matrix_world
        pts = [mw @ v.co for v in display.data.vertices]
        lo = Vector((min(p[a] for p in pts) for a in range(3)))
        hi = Vector((max(p[a] for p in pts) for a in range(3)))
        center = (lo + hi) / 2.0
        ext = [hi[a] - lo[a] for a in range(3)]
        order = sorted(range(3), key=lambda a: ext[a])
        diag = float(np.linalg.norm(ext)) or 1.0
        views = {}
        for name, axis_i in zip(PIECE_VIEWS, order):
            cam_data = bpy.data.cameras.new(f"pcam_{name}")
            # 裁剪面随组件尺度走：单件归一化后最长边 = 1，但**若该件本身极小**
            # （退化薄片 raw 尺寸可到 1e-3），固定 clip_start=0.1 会把整个组件裁掉。
            cam_data.clip_start = max(diag * 0.01, 1e-6)
            cam_data.clip_end = max(diag * 100.0, 1.0)
            cam = bpy.data.objects.new(f"pcam_{name}", cam_data)
            scene.collection.objects.link(cam)
            cam.location = center + axes[axis_i] * (diag * 2.6)
            target = bpy.data.objects.new(f"ptgt_{name}", None)
            target.location = center
            scene.collection.objects.link(target)
            track = cam.constraints.new("TRACK_TO")
            track.target = target
            scene.camera = cam
            views[name] = cam
        rank = os.path.splitext(os.path.basename(pf))[0]
        render_views(scene, views, list(views), f"piece_{rank}_", out_dir)
        obj.hide_render = False
        bpy.data.objects.remove(display)
        restore_lights(saved)
        print(f"[render_one] piece {rank} rendered ({len(views)} views)")


# ---------------------------------------------------------------- 定位图（PLAN-07）

def _emissive_material(name, hex_color: str, strength: float):
    """恒定色自发光材质：不受灯光影响，故三色在任何机位下都同色可辨。"""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.remove(nt.nodes["Principled BSDF"])
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*hex_to_linear(hex_color), 1.0)
    em.inputs["Strength"].default_value = float(strength)
    nt.links.new(em.outputs[0], nt.nodes["Material Output"].inputs["Surface"])
    return mat


def _ghost_material(name, hex_color: str, alpha: float):
    """半透明灰底幽灵：其余件用它渲染，提供整机轮廓但不遮挡被强调的两件。"""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*hex_to_linear(hex_color), 1.0)
    bsdf.inputs["Roughness"].default_value = 0.9
    bsdf.inputs["Alpha"].default_value = float(alpha)
    # Blender 4.2+ 用 surface_render_method；旧名 blend_method 仍在（两个都设，防版本漂移）
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    if hasattr(mat, "blend_method"):
        mat.blend_method = "BLEND"
    mat.use_backface_culling = False
    return mat


def _shared_face_flags(me_a, me_b, tol: float) -> list[bool]:
    """逐面判"是否落在另一件的表面上"（面质心到另一件表面的最近距离 <= tol）。

    **必须在任何几何偏移之前调用**（PLAN-07 §3.1 的硬约束）：偏移会改变距离，
    判据一旦在偏移后计算就被污染——而重叠率 100% 的检出两片表面完全重合，
    正是最容易踩这个坑的一类。
    """
    from mathutils.bvhtree import BVHTree
    verts_b = [v.co.copy() for v in me_b.vertices]
    polys_b = [tuple(pl.vertices) for pl in me_b.polygons]
    bvh = BVHTree.FromPolygons(verts_b, polys_b, all_triangles=False, epsilon=0.0)
    flags = []
    for poly in me_a.polygons:
        hit = bvh.find_nearest(poly.center, 1.0e9)
        dist = float(hit[3]) if hit and hit[3] is not None else float("inf")
        flags.append(dist <= tol)
    return flags


def _offset_along(me, direction, distance: float) -> None:
    """把网格整体平移 `direction * distance`（方向须为单位向量）。

    **为什么是"朝相机"而不是"沿法线"**（PLAN-07 §3.1 原写沿法线，此处按实测修正）：
    沿法线偏移时符号取决于该件法线的朝向——重合薄片的法线可能朝内，于是被推到灰底幽灵的
    表面**之后**，颜色与半透明灰底混合而发白（实测 p01#8：琥珀被冲成淡灰蓝，几乎看不出）。
    改为一律朝相机方向平移，A 比 B 再多偏一档，则两者必然都落在幽灵之前、且彼此不重合：
    既避免 z-fighting，也避免被幽灵冲淡。
    """
    for v in me.vertices:
        v.co = v.co + direction * distance


def render_locator_scene(items, ghost_glb, res, lookdev, out_dir, tol_ratio,
                         cfg_override=None):
    """逐检出定位图（PLAN-07 §3.1/§3.2）：半透明灰底 + 三色布尔拆分，单视图。

    `items`：`[{"rank":8, "neighbor_rank":4, "piece_glb":..., "neighbor_glb":..., "direction":[x,y,z]}]`
    （`direction` 由编排器侧的 `locator.choose_view` 用纯投影算出——本函数不做视角搜索。）

    每次只建 3 个对象（幽灵 + A + B），故 **342 件与 11 件同样便宜**。三色拆分用
    "面质心到另一件表面的最近距离"判定，**先判后偏**；A/B 再朝相机方向各偏一档，
    使"两片完全重合"这类检出也能分色（否则谁画在后谁赢，三色里有一色永远看不见）。
    """
    import math

    from mathutils import Vector

    from meshq.blender.lookdev import (CAM_LENS_MM, build_scene, render_views,
                         set_view_transform)
    from meshq.core.lookdev_math import locator_config, locator_lookdev

    lcfg = locator_config(lookdev)
    fit = str(lcfg.get("fit", "a"))
    if cfg_override:
        lcfg.update(cfg_override)
    bg_lookdev = locator_lookdev(lookdev)
    marks: dict[str, dict[str, list[float]]] = {}
    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y = res
    for it in items:
        clear_scene()
        ghost = import_glb(ghost_glb)
        mats_ghost = [_ghost_material("ghost", lcfg["ghost_color"],
                                      float(lcfg["ghost_alpha"]))]
        assign_materials(ghost, mats_ghost, [0] * len(ghost.data.polygons))
        obj_a = import_glb(it["piece_glb"])
        obj_b = import_glb(it["neighbor_glb"]) if it.get("neighbor_glb") else None
        # 运行期守卫：三个对象必须各不相同。曾因误用"join 场景里全部网格"的导入函数，
        # A/B/幽灵被合成同一个对象（面数相同、包围盒相同），三色拆分随之全错且不报错。
        n_a, n_b = len(obj_a.data.polygons), (len(obj_b.data.polygons) if obj_b else 0)
        n_ghost = len(ghost.data.polygons)
        # 判据取"不多于"：manifest 的面数是 trimesh 口径（weld 1e-5），而此处的导入会做
        # 1e-4 焊接，退化件可能被再合并而**变少**（p01 的 2 面片导入后为 1 面，实测）。
        # 焊接只减不增，故 "n > 期望" 必然是"与其它对象合并了"这一类错误。
        for label, got, exp, path in (("组件", n_a, it.get("expect_faces_a"), it["piece_glb"]),
                                      ("邻居", n_b, it.get("expect_faces_b"),
                                       it.get("neighbor_glb"))):
            if exp and got > exp:
                raise RuntimeError(
                    f"定位图导入异常：{label} {path} 期望 ≤{exp} 面、实际 {got} 面"
                    "（疑与其它对象合并，见 bpy_geom.import_glb 说明）")
        print(f"[render_one] locator rank={it['rank']} 面数 幽灵={n_ghost} "
              f"组件={n_a} 邻居={n_b}")

        # 归一化口径：幽灵 + A + B 的**联合包围盒**（与整机渲染等价，只换计算来源）
        pts = [ghost.matrix_world @ v.co for v in ghost.data.vertices]
        pts += [obj_a.matrix_world @ v.co for v in obj_a.data.vertices]
        if obj_b:
            pts += [obj_b.matrix_world @ v.co for v in obj_b.data.vertices]
        lo = Vector((min(p[a] for p in pts) for a in range(3)))
        hi = Vector((max(p[a] for p in pts) for a in range(3)))
        center = (lo + hi) / 2.0
        longest = max((hi[a] - lo[a]) for a in range(3)) or 1.0
        diag = float((hi - lo).length)
        for o in (ghost, obj_a, obj_b):
            if o is None:
                continue
            for v in o.data.vertices:
                v.co = (v.co - center) / longest

        tol = float(tol_ratio) * diag / longest      # 比例量与尺度无关
        offset = float(lcfg["offset_ratio"]) * diag / longest

        # 眼位方向先算出来（偏移要用它）
        d = Vector(tuple(float(x) for x in it["direction"]))
        if d.length_squared < 1e-12:
            d = Vector((0.0, -1.0, 0.3))
        d.normalize()

        # **先判定（原始几何），再偏移**——顺序是硬约束，反了判据就被自己污染
        if obj_b is not None:
            a_shared = _shared_face_flags(obj_a.data, obj_b.data, tol)
            b_shared = _shared_face_flags(obj_b.data, obj_a.data, tol)
        else:
            a_shared = [False] * len(obj_a.data.polygons)
            b_shared = []
        _offset_along(obj_b.data, d, offset) if obj_b is not None else None
        _offset_along(obj_a.data, d, offset * 2.0)

        mat_slots = [_emissive_material("only_a", lcfg["color_only_a"],
                                        float(lcfg["emission_strength"])),
                     _emissive_material("shared", lcfg["color_shared"],
                                        float(lcfg["emission_strength"]))]
        assign_materials(obj_a, mat_slots, [1 if sh else 0 for sh in a_shared])
        if obj_b is not None:
            # 邻居整体一个色，**不拆 B 侧**：判据是"面质心到另一件表面的距离"，
            # 能测出"小面落在多大面上"，测不出"大面被小面盖住"——A 远小于 B 的面时
            # B 侧会全判为"未重叠"（p01#8 实测：B 的 513 面全落进 only_b，品红一个不见）。
            # 重叠那一片由 A 侧承担（A 的面小，判据可靠）：品红=与邻居重合的部分。
            b_slots = [_emissive_material("only_b", lcfg["color_only_b"],
                                          float(lcfg["emission_strength"]))]
            assign_materials(obj_b, b_slots, [0] * len(obj_b.data.polygons))

        build_scene(scene, bg_lookdev, None)
        set_view_transform(scene, lookdev.get("highlight_view_transform", "Standard"))
        scale_lights(scene, float(lcfg.get("light_scale", 1.0)))

        # 眼位：方向由编排器给定；距离保证整机在画内，瞄准被强调两件的中心
        focus = Vector((0.0, 0.0, 0.0))   # 归一化后整机中心在原点；细取景会被覆盖
        # 取景以**被强调的两件**为准（PLAN-07 §3.3 只说了"整机在画内"，此处按实测修正）：
        # 整机取景下退化薄片只有约 4 px（p01#8：件对角线是模型的 0.44%），"在哪、叠了多少"
        # 根本看不出来。改为让 A∪B 的包围球填充画幅，灰度幽灵自然延伸到画外作上下文。
        pair_pts = []
        for o in (obj_a, obj_b):
            if o is None:
                continue
            mw = o.matrix_world
            pair_pts += [mw @ v.co for v in o.data.vertices]
        # 两个机位，缺一不可（实测结论）：
        # - 整机取景回答"在哪"，但退化薄片只有约 4 px，看不出"叠的是哪一片"；
        # - 贴近组件回答"叠的是哪一片"（品红就是重合面），但看不到它在整机的什么位置。
        # 二者不可兼得（组件可小到模型的 0.44%），故同一方向渲两张，交付层横向拼成一张。
        tan_half = math.atan(18.0 / CAM_LENS_MM)         # 36mm 传感器半宽
        a_pts = [obj_a.matrix_world @ v.co for v in obj_a.data.vertices]
        shots = []
        # ① 整机上下文
        shots.append(("model", Vector((0.0, 0.0, 0.0)),
                      float(lcfg["distance"]) / max(1e-6, float(lcfg["zoom"]))))
        # ② 贴近组件（其包围球填充画幅，幽灵与邻居自然延伸到画外作上下文）
        if a_pts:
            pc = Vector((sum(p[a] for p in a_pts) / len(a_pts) for a in range(3)))
            radius = max((p - pc).length for p in a_pts) or 1e-4
            shots.append(("close", pc,
                          min(radius / tan_half * float(lcfg["margin"]),
                              float(lcfg["distance"]))))
        cams = {}
        for name, focus, dist in shots:
            cam_data = bpy.data.cameras.new(f"loc_cam_{name}")
            cam_data.lens = CAM_LENS_MM
            cam_data.clip_start = 0.005
            cam = bpy.data.objects.new(f"loc_cam_{name}", cam_data)
            scene.collection.objects.link(cam)
            cam.location = focus + d * dist
            tgt = bpy.data.objects.new(f"loc_tgt_{name}", None)
            tgt.location = focus
            scene.collection.objects.link(tgt)
            cam.constraints.new("TRACK_TO").target = tgt
            cams[name] = cam
        render_views(scene, cams, tuple(cams), f"locator_{it['rank']}_", out_dir)
        # 记录组件在各机位下的**投影像素框**：整机取景下小组件可能只有几像素，
        # 交付层据此画一个固定像素大小的标记环，"在哪"才真的找得到。
        from bpy_extras.object_utils import world_to_camera_view
        for name, cam in cams.items():
            us = [world_to_camera_view(scene, cam, obj_a.matrix_world @ v.co)
                  for v in obj_a.data.vertices]
            if not us:
                continue
            xs = [u.x * scene.render.resolution_x for u in us]
            ys = [(1.0 - u.y) * scene.render.resolution_y for u in us]
            marks.setdefault(str(it["rank"]), {})[name] = [
                round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)]
        print(f"[render_one] locator rank={it['rank']} "
              f"neighbor={it.get('neighbor_rank')} view={it.get('view')} "
              f"dir=({d.x:.2f},{d.y:.2f},{d.z:.2f}) shots={list(cams)}")
    with open(os.path.join(out_dir, "locator_marks.json"), "w",
              encoding="utf-8") as f:
        json.dump(marks, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- 编排

def main():
    (in_glb, out_dir, checks_path, res_x, res_y, diag_ratio, lookdev,
     parts) = parse_args()
    channels = set(parts.get("channels") or ("base", "highlight", "wire"))
    # 判别档位（bpy rank → "A"/"C"），JSON 键为字符串；缺省 = 无档位可着色（全灰）
    tiers = {int(k): v for k, v in (parts.get("tiers") or {}).items()}
    res = (res_x, res_y)
    os.makedirs(out_dir, exist_ok=True)
    clear_scene()
    obj = import_and_weld(in_glb)
    me = obj.data

    n_nontri = sum(1 for p in me.polygons if len(p.vertices) != 3)
    print(f"[render_one] non-triangle faces: {n_nontri}/{len(me.polygons)}")

    pieces, face_piece = sort_pieces(*find_pieces(me))
    classify_pieces(pieces, diag_ratio)
    n_small = sum(1 for p in pieces if p["is_small"])
    total_faces = len(me.polygons)
    small_faces = sum(p["n_faces"] for p in pieces if p["is_small"])
    small_pids = {p["pid"] for p in pieces if p["is_small"]}

    zero_faces, zero_edges = count_degenerate(me)
    edge_classes = count_edge_classes(me)
    checks = {
        "source": os.path.basename(in_glb),
        "n_verts": len(me.vertices),
        "n_faces": total_faces,
        # 口径变更（PLAN-03 §3.1）：开放边界与真非流形分列。历史字段
        # `non_manifold_edges` 曾把两者合计（= 两者之和），语义已收窄为计数>2。
        "boundary_edges": edge_classes["boundary_edges"],
        "non_manifold_edges": edge_classes["non_manifold_edges"],
        "self_intersect_faces": count_self_intersect_faces(me),
        "zero_area_faces": zero_faces,
        "zero_length_edges": zero_edges,
        "n_pieces": len(pieces),
        "n_small_pieces": n_small,
        "fragment_face_ratio": round(small_faces / total_faces, 6) if total_faces else 0.0,
        # 全量落盘（不再截断到 20）：p18@standard 有 23~24 个组件，截断会让尾部组件
        # 在任何跨引擎对账里凭空消失——与 metrics 侧曾发生的截断事故同类（memory 2026-09-21）。
        "pieces": [{k: p[k] for k in ("n_verts", "n_faces", "diag", "volume",
                                      "diag_ratio_to_main", "is_small")}
                   for p in pieces],
        "thresholds": {"diag_ratio": diag_ratio, "weld": WELD_THRESHOLD,
                       "zero": ZERO_THRESHOLD},
        "blender_version": bpy.app.version_string,
    }

    norm_info, ground_z = normalize(obj)
    checks["normalize"] = norm_info
    # 降采样决策只依赖面数，元数据必须在 json.dump 之前入 dict（否则永不落盘）
    wire_faces = int(lookdev["wire_display_faces"])
    needs_decimate = total_faces > wire_faces
    if needs_decimate:
        checks["wire_display"] = {"decimated_from_faces": total_faces,
                                  "ratio": round(wire_faces / total_faces, 6),
                                  "ssaa": int(lookdev["wire_ssaa"]),
                                  "note": "线框为展示用降采样副本，非原始网格密度"}
    elif int(lookdev["wire_ssaa"]) != 1:
        checks["wire_display"] = {"ssaa": int(lookdev["wire_ssaa"])}
    with open(checks_path, "w", encoding="utf-8") as f:
        json.dump(checks, f, ensure_ascii=False, indent=1)

    scene = bpy.context.scene
    scene.render.resolution_x = res_x
    scene.render.resolution_y = res_y
    scene.render.image_settings.file_format = "PNG"
    engine_base = set_engine(scene, ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"))
    cams = build_scene(scene, lookdev, ground_z)
    mats = make_materials(lookdev)

    if "base" in channels:
        render_base_channel(scene, cams, obj, mats, len(face_piece), out_dir, lookdev)
    if "highlight" in channels:
        render_highlight_channel(scene, cams, obj, mats, me, pieces, face_piece,
                                 tiers, res, out_dir, lookdev)
    if "wire" in channels:
        render_wire_channel(scene, cams, obj, mats, total_faces, needs_decimate,
                            wire_faces, res, out_dir, lookdev)

    piece_files = parts.get("piece_files") or []
    if piece_files:
        render_pieces_isolated(piece_files, res, lookdev, out_dir)

    locator_path = parts.get("locator_items_path")
    if locator_path and os.path.isfile(locator_path):
        with open(locator_path, encoding="utf-8") as f:
            locator_items = json.load(f)
        if locator_items:
            render_locator_scene(locator_items, parts["locator_ghost"], res, lookdev,
                                 out_dir, parts.get("locator_tol_ratio", 0.001))

    print(f"[render_one] engine={engine_base} pieces={len(pieces)} small={n_small} "
          f"tiered={len(tiers)} channels={sorted(channels)}")


main()
