"""lookdev 展示侧（bpy）：世界背景、三点灯、地平面、相机族、材质与渲染。

只能经 `<blender.exe> --background --python` 入口执行（依赖 bpy）。纯逻辑（色彩空间、
灯位数学、视图表、图例记录）在 lookdev_math.py；本模块只负责建对象、配材质、渲图。

自 render_one.py 抽出（PLAN-03.1），迁移时保留的约束：
- 灯位用 lookdev_math.area_light_position 的球坐标（方位角 0 = 正前 -Y），TRACK_TO 对准原点；
- 观变换分通道设置：AgX 会把红色自发光压成粉，highlight 通道用 Standard（PLAN-02 D3）；
- 线壳通道的「DECIMATE 必须在 WIREFRAME 之前」属通道编排，留在 render_one.py，
  改线壳相关代码前必读 HANDOFF 红线。
"""

from __future__ import annotations

import os

import bpy

from meshq.core.lookdev_math import VIEWS, area_light_position, hex_to_linear

GROUND_COLOR = "#3A3D42"   # 地平面底色（PLAN-02 §2.1：深灰漫反射，提供接触阴影）
CAM_LENS_MM = 50           # 四视图共用焦距


# ---------------------------------------------------------------- 世界与灯光

def world_background(scene, lookdev: dict) -> None:
    """背景：'solid' 单色，或默认的上浅下深纵向渐变。

    **幂等重建**：先清空世界节点树再按模式重建。不能只改 Background 节点——
    节点树跨通道复用（同一次 Blender 进程里 base 通道先建了渐变链路），
    残留的 `ColorRamp → Background.Color` 连线会**覆盖** solid 模式下设置的
    default_value，于是"设了纯白背景渲染出来还是渐变"（2026-09-23 单件图证实测）；
    同时渐变分支每次调用都 `nodes.new`，不清空还会逐次堆积节点。
    """
    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.name = "Background"
    bg.inputs[1].default_value = 1.0
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    if lookdev["background_mode"] == "solid":
        bg.inputs[0].default_value = (*hex_to_linear(lookdev["bg_solid_color"]), 1.0)
        return
    # 上浅下深纵向渐变：Generated(视线方向).Z → [-1,1]→[0,1] → ColorRamp
    tex = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    rng = nt.nodes.new("ShaderNodeMapRange")
    rng.inputs["From Min"].default_value = -1.0
    rng.inputs["From Max"].default_value = 1.0
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*hex_to_linear(lookdev["bg_gradient_bottom"]), 1.0)
    ramp.color_ramp.elements[1].color = (*hex_to_linear(lookdev["bg_gradient_top"]), 1.0)
    nt.links.new(tex.outputs["Generated"], sep.inputs["Vector"])
    nt.links.new(sep.outputs["Z"], rng.inputs["Value"])
    nt.links.new(rng.outputs["Result"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])


def add_area_light(scene, target, name, energy, color_hex, az_deg, el_deg, dist, size):
    """AREA 灯：球坐标摆位（方位角 0=正前 -Y，负值=画幅左），TRACK_TO 对准原点。"""
    data = bpy.data.lights.new(name, type="AREA")
    data.energy = energy
    data.size = size
    data.color = hex_to_linear(color_hex)
    ob = bpy.data.objects.new(name, data)
    ob.location = area_light_position(az_deg, el_deg, dist)
    scene.collection.objects.link(ob)
    con = ob.constraints.new("TRACK_TO")
    con.target = target
    return ob


def build_scene(scene, lookdev: dict, ground_z: float | None) -> dict:
    """渐变背景 + 暖主光/冷轮廓光/中性补光 + 地平面 + 相机族（各通道复用）。

    返回 {view: camera} 字典，视图顺序与 lookdev_math.VIEWS 一致。
    """
    world_background(scene, lookdev)

    target = bpy.data.objects.new("cam_target", None)
    scene.collection.objects.link(target)

    add_area_light(scene, target, "key", lookdev["key_energy"], lookdev["key_color"],
                   lookdev["key_azimuth"], lookdev["key_elevation"], 3.2, 2.5)
    add_area_light(scene, target, "rim", lookdev["rim_energy"], lookdev["rim_color"],
                   150.0, 40.0, 3.2, 2.0)
    add_area_light(scene, target, "fill", lookdev["fill_energy"], "#FFFFFF",
                   40.0, 10.0, 3.5, 4.0)

    if lookdev["ground_plane"] and ground_z is not None:
        bpy.ops.mesh.primitive_plane_add(size=8.0, location=(0, 0, ground_z - 0.002))
        ground = bpy.context.active_object
        mat = bpy.data.materials.new("ground")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = (*hex_to_linear(GROUND_COLOR), 1.0)
        bsdf.inputs["Roughness"].default_value = 0.9
        ground.data.materials.append(mat)

    cams = {}
    for view, loc in VIEWS.items():
        cam_data = bpy.data.cameras.new(view)
        cam_data.lens = CAM_LENS_MM
        cam = bpy.data.objects.new(view, cam_data)
        cam.location = loc
        scene.collection.objects.link(cam)
        con = cam.constraints.new("TRACK_TO")
        con.target = target
        cams[view] = cam
    return cams


# ---------------------------------------------------------------- 材质

def make_materials(lookdev: dict):
    """灰模、缺陷红（暗底 + 纯红自发光）、线壳深色（无光影恒定色）、待审琥珀。

    红 = A 档（自动删候选），琥珀 = C 档（待审）——判别结果着色（PLAN-03 D10，
    可读性修复 2026-09-21：红色不再按 is_small 几何规则涂色）。
    """
    gray = bpy.data.materials.new("base_gray")
    gray.use_nodes = True
    bsdf = gray.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.65, 0.65, 0.65, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.55

    red = bpy.data.materials.new("defect_red")
    red.use_nodes = True
    bsdf = red.node_tree.nodes["Principled BSDF"]
    # 底色压暗；观变换由 highlight 通道统一为 Standard（AgX 会把红压成粉，PLAN-02 D3）
    bsdf.inputs["Base Color"].default_value = (0.05, 0.0, 0.0, 1.0)
    if "Emission Color" in bsdf.inputs:
        bsdf.inputs["Emission Color"].default_value = (1.0, 0.03, 0.03, 1.0)
        bsdf.inputs["Emission Strength"].default_value = float(
            lookdev["emission_strength"])

    amber = bpy.data.materials.new("review_amber")
    amber.use_nodes = True
    bsdf = amber.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.05, 0.03, 0.0, 1.0)
    if "Emission Color" in bsdf.inputs:
        bsdf.inputs["Emission Color"].default_value = (1.0, 0.62, 0.10, 1.0)
        bsdf.inputs["Emission Strength"].default_value = float(
            lookdev["emission_strength"])

    wire = bpy.data.materials.new("wire_dark")
    wire.use_nodes = True
    nt = wire.node_tree
    nt.nodes.remove(nt.nodes["Principled BSDF"])
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*hex_to_linear(lookdev["wire_color"]), 1.0)
    em.inputs["Strength"].default_value = 1.0
    nt.links.new(em.outputs[0], nt.nodes["Material Output"].inputs["Surface"])
    return gray, red, wire, amber


def assign_materials(obj, mats, face_slot):
    """按面分配材质槽：mats = 槽位材质表，face_slot = 逐面槽号（与 obj.data.polygons 对齐）。"""
    obj.data.materials.clear()
    for mat in mats:
        obj.data.materials.append(mat)
    n = len(mats)
    for poly, slot in zip(obj.data.polygons, face_slot):
        poly.material_index = slot if 0 <= slot < n else 0


def scale_lights(scene, factor: float) -> list:
    """按比例缩放场景中全部灯光能量，返回快照供 restore_lights 还原。

    用于 highlight 通道：Standard 观变换不压缩高光，灯光强度若保持 AgX 调校值会
    把灰体直接过曝成白剪影（可读性修复 2026-09-21）。
    """
    saved = []
    for ob in scene.objects:
        if ob.type == "LIGHT":
            saved.append((ob.data, ob.data.energy))
            ob.data.energy = ob.data.energy * factor
    return saved


def restore_lights(saved) -> None:
    for light, energy in saved:
        light.energy = energy


def set_view_transform(scene, name: str) -> None:
    """观变换。不支持的取值回退 Standard，保证换 Blender 版本不炸。"""
    try:
        scene.view_settings.view_transform = name
    except TypeError:
        scene.view_settings.view_transform = "Standard"


def set_engine(scene, candidates) -> str:
    """按候选顺序尝试设置渲染引擎，返回生效的引擎名。"""
    for engine in candidates:
        try:
            scene.render.engine = engine
            return engine
        except TypeError:
            continue
    raise RuntimeError(f"无可用渲染引擎：{candidates}")


def render_views(scene, cams, views: tuple[str, ...], prefix: str, out_dir: str) -> None:
    """逐个视图切相机并写 PNG：<out_dir>/<prefix><view>.png。"""
    for view in views:
        scene.camera = cams[view]
        scene.render.filepath = os.path.join(out_dir, f"{prefix}{view}.png")
        bpy.ops.render.render(write_still=True)
