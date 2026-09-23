"""Blender 侧模块包（只经 `blender.exe --background --python` 进入，不在虚拟环境里 import）。

为什么单独一层：这一层的模块运行在 Blender 内置 Python 里，**依赖方向是单向的**——
它们可以 import `meshq/core` 下的纯逻辑模块（`lookdev_math` / `geometry`），反之不行。
此前这条边界只写在文档里，于是清理提交删掉 `lookdev.assign_piece_materials` 时没人意识到
Blender 侧的 import 会断（`.claude/memory/2026-09-23-render-one-dangling-imports.md`）。

- `render_one.py`：单模型渲染入口（bpy 检查 + 三通道渲染 + 单件独立渲染 + 定位图）
- `lookdev.py`：展示侧（世界背景、灯光、材质、相机族、渲染）
- `bpy_geom.py`：几何侧（导入焊接、连通域、几何计数、归一化、碎片像素投影）
"""
