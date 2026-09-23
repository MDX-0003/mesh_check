"""几何侧（bpy）：导入焊接、连通域、几何计数、归一化、像素投影。

只能经 `<blender.exe> --background --python` 入口执行（依赖 bpy/bmesh/mathutils）。
与展示无关——世界/灯光/相机/材质在 lookdev.py；纯数学与视图表在 lookdev_math.py。

自 render_one.py 抽出（PLAN-03.1）。各函数与官方插件的对应关系：
  import_and_weld   ↔ Bridge 导入后的 remove_doubles（官方插件 v0.6.1）
  find_pieces       ↔ get_loose_parts 语义
  count_edge_classes ↔ analyze.py 的非流形检查（口径经 PLAN-03 §3.1 拆分）
  count_self_intersect_faces ↔ lib.py bmesh_check_self_intersect_object
  normalize         ↔ operators/edit.py scale_to_bounds
改这些函数等于改双引擎对账的口径，动手前先看其单测与 meshq/stages/metrics.py 的对应实现。
"""

from __future__ import annotations

import bpy
import bmesh
import mathutils
from bpy_extras import object_utils

from meshq.core.geometry import bbox_stats, classify_edge_counts

WELD_THRESHOLD = 1e-4   # 米；与插件 Make Manifold 默认 Merge Distance 0.0001 一致
ZERO_THRESHOLD = 1e-4   # 米；与插件 threshold_zero 默认 0.01cm 一致


def clear_scene() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def import_and_weld(in_glb: str):
    """导入 GLB → 合并多网格 → 应用变换 → 焊接（UV 接缝分裂的顶点）。"""
    bpy.ops.import_scene.gltf(filepath=in_glb)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    # 焊接 UV 接缝分裂的顶点（同插件 Bridge 导入后的 remove_doubles）
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=WELD_THRESHOLD)
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def import_glb(in_glb: str):
    """导入 GLB 并只合并**本次导入**的网格（`import_and_weld` 的多对象版）。

    为什么必须区分：`import_and_weld` 取的是"场景里全部 MESH"，重复调用会把先前已导入的
    对象一起 join 进去。定位图要同时放幽灵 + 组件 + 邻居三个对象，用它就会让三个对象变成
    同一个（2026-09-23 实测：A 与 B 面数相同、包围盒相同，三色里永远只看得见一色）。

    焊接也只落在本次导入的网格内——这正是"重合薄片被 1e-4 焊接并入主体"那条老问题的
    成因，跨对象焊接在这里绝对不能发生。
    """
    before = {o.name for o in bpy.context.scene.objects if o.type == "MESH"}
    bpy.ops.import_scene.gltf(filepath=in_glb)
    new = [o for o in bpy.context.scene.objects
           if o.type == "MESH" and o.name not in before]
    if not new:
        raise RuntimeError(f"未导入任何网格：{in_glb}")
    bpy.ops.object.select_all(action="DESELECT")
    for o in new:
        o.select_set(True)
    bpy.context.view_layer.objects.active = new[0]
    if len(new) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=WELD_THRESHOLD)
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def count_edge_classes(me) -> dict[str, int]:
    """无向边 → 相邻面计数 → 开放边界与真非流形**分列**（口径定义在 geometry.classify_edge_counts）。

    口径变更（PLAN-03 §3.1）：历史实现返回 `计数 != 2` 的单个合并值，把"开放边界（计数=1）"
    与"真非流形（计数>2）"算在一起。T2（smart-topology）低模大量存在开放边界——实测
    p01@smart-topology 的 35 条里 29 条是边界——合并计数会让整批 T2 模型被判成"有缺陷"。
    现在两者分列，凡当缺陷信号用的场合只吃 `non_manifold_edges`。
    """
    counts: dict[tuple[int, int], int] = {}
    for poly in me.polygons:
        n = len(poly.vertices)
        for i in range(n):
            a, b = int(poly.vertices[i]), int(poly.vertices[(i + 1) % n])
            key = (a, b) if a < b else (b, a)
            counts[key] = counts.get(key, 0) + 1
    return classify_edge_counts(counts.values())


def find_pieces(me):
    """顶点连通域（BFS，同插件 get_loose_parts 语义）。

    返回 (pieces, face_piece)：
      pieces 未排序，含 pid/n_verts/n_faces/diag/volume；
      face_piece[i] = 第 i 个多边形所属的 piece pid。
    """
    n = len(me.vertices)
    adj = [[] for _ in range(n)]
    for edge in me.edges:
        a, b = int(edge.vertices[0]), int(edge.vertices[1])
        adj[a].append(b)
        adj[b].append(a)

    piece_id = [-1] * n
    pieces = []
    for start in range(n):
        if piece_id[start] != -1:
            continue
        pid = len(pieces)
        verts = []
        stack = [start]
        piece_id[start] = pid
        while stack:
            v = stack.pop()
            verts.append(v)
            for nb in adj[v]:
                if piece_id[nb] == -1:
                    piece_id[nb] = pid
                    stack.append(nb)
        pieces.append({"pid": pid, "verts": verts, "n_verts": len(verts),
                       "n_faces": 0, "diag": 0.0, "volume": 0.0})

    face_piece = []
    for poly in me.polygons:
        pid = piece_id[int(poly.vertices[0])]
        face_piece.append(pid)
        pieces[pid]["n_faces"] += 1

    for piece in pieces:
        coords = [tuple(me.vertices[v].co) for v in piece["verts"]]
        stats = bbox_stats(coords)
        piece["diag"] = stats["diag"]
        piece["volume"] = stats["volume"]
    return pieces, face_piece


def sort_pieces(pieces, face_piece):
    """按面数降序排序；返回 (sorted_pieces, 重映射后的 face_piece)。"""
    order = sorted(range(len(pieces)), key=lambda i: -pieces[i]["n_faces"])
    old_to_new = {old: new for new, old in enumerate(order)}
    sorted_pieces = [pieces[old] for old in order]
    return sorted_pieces, [old_to_new[p] for p in face_piece]


def count_self_intersect_faces(me) -> int:
    """BVHTree 自相交面数（同插件 lib.py bmesh_check_self_intersect_object）。"""
    if not me.polygons:
        return 0
    bm = bmesh.new()
    bm.from_mesh(me)
    tree = mathutils.bvhtree.BVHTree.FromBMesh(bm, epsilon=0.00001)
    overlap = tree.overlap(tree)
    faces = {i for pair in overlap for i in pair}
    bm.free()
    return len(faces)


def count_degenerate(me) -> tuple[int, int]:
    """退化面（面积 ≤ 阈值²）与退化边（长度 ≤ 阈值）计数。"""
    zero_faces = sum(1 for p in me.polygons if p.area <= ZERO_THRESHOLD * ZERO_THRESHOLD)
    zero_edges = 0
    for e in me.edges:
        a = me.vertices[e.vertices[0]].co
        b = me.vertices[e.vertices[1]].co
        if (a - b).length <= ZERO_THRESHOLD:
            zero_edges += 1
    return zero_faces, zero_edges


def normalize(obj) -> tuple[dict, float]:
    """平移 bbox 中心到原点 + 最长边 → 1（插件 scale_to_bounds 的 headless 复刻）。

    只作用于渲染场景内存中的副本；原始 GLB 永不修改，原始 bbox 全程记录。
    另返回归一化后的 bbox 底部 z（地平面摆放用）。
    """
    me = obj.data
    coords = [v.co for v in me.vertices]
    mins = mathutils.Vector((min(c.x for c in coords), min(c.y for c in coords),
                             min(c.z for c in coords)))
    maxs = mathutils.Vector((max(c.x for c in coords), max(c.y for c in coords),
                             max(c.z for c in coords)))
    center = (mins + maxs) / 2
    longest = max(maxs.x - mins.x, maxs.y - mins.y, maxs.z - mins.z)
    scale = 1.0 / longest if longest > 0 else 1.0
    mat = mathutils.Matrix.Translation(-center) @ mathutils.Matrix.Scale(scale, 4)
    me.transform(mat)
    info = {"scale_applied": scale,
            "raw_bbox_min": tuple(mins), "raw_bbox_max": tuple(maxs)}
    return info, (mins.z - center.z) * scale


