"""Mesh-Test：生成式 3D 资产几何质检管线。

包内分层（依赖方向单向，勿逆向）：

    meshq/core/      纯逻辑与契约：配置 / 几何口径 / 检出契约 / 拆分产物 / 展示常量
    meshq/stages/    管线六段：generate → render → metrics → detect → report → deliver
    meshq/blender/   只在 Blender 内置 Python 里运行（渲染与 bpy 原生指标）
    meshq/tools/     按需运行的辅助工具：备份 / 审核台 / 像素回归 / 数据装载
    meshq/pipeline.py  统一入口（各 stage 的编排与 --dry-run）

入口命令一律从仓库根执行：`python -m meshq.pipeline <stage>`、`python -m meshq.stages.render …`、
`python -m meshq.tools.review_server …`。
"""
