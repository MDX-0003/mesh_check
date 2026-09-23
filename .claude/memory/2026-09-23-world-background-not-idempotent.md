# `world_background` 非幂等：设了纯色背景却渲出渐变

- **日期**：2026-09-23
- **现象**：`config` 里设 `background_mode = "solid"` + 纯白 `#FFFFFF`，渲出来仍是渐变。
  实测背景像素取样得到 (97,103,111) 与 (157,162,169) 两个值——正是 `bg_gradient_bottom`
  及渐变区间内的色。
- **根因**：`world_background` 的 solid 分支只写 `bg.inputs[0].default_value`；而 Blender
  的节点输入**存在连线时连线优先**，节点树又是跨通道复用的（同一次 Blender 进程里
  base 通道先建过渐变链路），残留的 `ColorRamp → Background.Color` 连线覆盖了纯色设定。
  另外渐变分支每次调用都 `nodes.new`，不清空会逐次堆积节点。
- **修复**：清空世界节点树后按模式重建（OutputWorld + Background + 按需渐变链）。
- **预防**：任何"按配置改节点树"的函数都要**先清空再建**，不要假设节点树是干净的；
  同类症状（"设了 A 却渲出 B"）先查残留连线，再怀疑参数。
- **关键词**：blender, world, node_tree, 幂等, 连线优先, 背景, 渐变

