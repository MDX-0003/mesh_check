# trimesh.split 抛 "no graph engines available!"

- **日期**：2026-09-21
- **现象**：`mesh.split(only_watertight=False)` 抛 `ImportError: no graph engines available!`，连通域统计全部失败。
- **根因**：trimesh 的图类操作（split/connected components）依赖外部图引擎（networkx 或 scipy），本项目 pyproject 只装了 trimesh+numpy，未装任何图引擎。
- **修复**：`uv add networkx`（纯 Python 轮子，任何 python 版本可装）。
- **预防**：引入 trimesh 时默认把 networkx 列为必装伴生依赖；新增涉及图/几何操作的依赖时，先在最小脚本里跑一遍再进 pyproject。
