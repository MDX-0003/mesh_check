# Blender 侧悬空导入让整个渲染链不可用（单测拦不到）

- **日期**：2026-09-23
- **现象**：`python -m meshq.stages.render --keys <key> --target pieces --force` 立刻失败，
  Blender stderr 为
  `ImportError: cannot import name 'assign_piece_materials' from 'lookdev'`；
  修掉这一条后紧接着又报第二条 `cannot import name 'legend_record' from 'lookdev_math'`。
  即**所有**渲染目标（overview / highlight / wire / pieces / locator / all）都在
  `render_one.py` 的 import 阶段就崩，`pytest` 全绿也照样崩。
- **根因**：PLAN-07 §五 清理（`d718540`）删掉了 `lookdev.assign_piece_materials`（每件一色）
  与 `lookdev_math.legend_record`（HTML 图例）两个函数，但 Blender 侧唯一入口
  `render_one.py` 的 import 行没同步——两个名字在渲染脚本体内**本来就没有任何调用**，
  纯死引用，所以删函数的人看不见、跑单测的人也看不见。
- **修复**：删掉这两个未使用的导入（`a7b312e`）；随后 `--target pieces` 与
  `--target locator` 各实跑一个模型（p05，4 件）确认出图正常。
- **预防**：**"删一个函数"必须同时扫它的 import 点**；`render_one.py` 这类**只在 Blender
  内 import 的脚本不在任何单测的导入路径上**，它的 import 健康度只能靠"实跑一次渲染"
  发现。清理类提交（删模块/删函数）落地后，最小验收动作 = 跑一次最便宜的渲染目标，
  哪怕只渲一个模型——这与 `note-render-delivery-checklist` 的"先渲一个样本再批量"同源，
  区别是这里连"样本跑不起来"都没人知道。
- **关键词**：blender, 悬空导入, ImportError, 清理遗漏, render_one, 单测盲区
