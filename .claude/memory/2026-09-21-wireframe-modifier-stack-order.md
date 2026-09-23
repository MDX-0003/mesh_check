# Wireframe 修改器栈序颠倒导致渲染 hang（DECIMATE 必须在 WIREFRAME 之前）

- **日期**：2026-09-21
- **现象**：p18（1,554,492 面）渲染稳定超时：600s/900s 两次 `[fail] 超时`。日志显示
  base/highlight 通道秒级出图、`highlight_regions.json` 已落盘，随后线框通道 >10 分钟
  无任何产出（`wire_*.png` mtime 不更新），Blender 进程被 subprocess 超时强杀。
- **根因**：`render_one.py` 里修改器添加顺序颠倒——先 `WIREFRAME` 后 `DECIMATE`。
  修改器按栈序求值，线壳修改器直接作用在 155 万面原网格上（每条边生成 4 边面壳），
  正是 HANDOFF 红线"禁止对百万面原网格用 Wireframe modifier"；之后再 DECIMATE 也救不回来。
- **修复**：先加 `DECIMATE`（降到 `wire_display_faces`）再加 `WIREFRAME`；修复后 p18
  全程约 8 分钟完成。
- **预防**：给 Blender 对象叠修改器时，把"降负载修改器"放在"扩展几何修改器"之前；
  涉及面数悬殊的副本操作，先查 HANDOFF 的性能红线。渲染脚本对单模型的耗时异常
  （通道级 mtime 停更）应优先怀疑求值规模而非渲染本身。
