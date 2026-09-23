# 定位图（`--target locator`）的渲染事实与调参入口

- **日期**：2026-09-23
- **是什么**：每条待复核检出渲一张"这个组件在模型的哪里、和谁重叠、重叠的是哪一片"的
  图（PLAN-07）。**半透明灰底 + 重叠面分色 + 双机位**。

## 关键事实（改代码前必读）

1. **一次只强调一个组件**，且其余件走半透明灰底 ⇒ **遮挡不再构成约束**，
   视角选择退化为**纯投影打分**（不需要任何射线投射/可见性测量）。
   `meshq/core/locator.py` 是纯逻辑（Fibonacci 方向候选 + 4 个标准视图锚点 + 标准视图优先条款），
   方向以**单位向量**跨进程传给 Blender——两侧无面序耦合。
2. **每个检出渲两个机位**（`locator_<rank>_model.png` / `_close.png`），交付层横向拼成
   `images/locator_<rank>.jpg`。原因：组件可小到模型的 **0.44%**（p01#8 整机取景下约 4 px），
   单张图无法同时交代"在哪"与"叠的是哪一片"。
3. **标记环**：`render_one` 用 `world_to_camera_view` 记录组件在各机位的投影像框，
   落 `locator_marks.json`；交付层据它在整机面板上画一个**最小半径兜底**的环——
   否则小组件在整机取景下找不到。
4. **三色语义**：琥珀 = 组件未重叠部分；品红 = 与邻居重合的那一片；青 = 邻居。
   **邻居侧不拆色**（面质心判据测不出"大面被小面盖住"，详见
   `2026-09-23-centroid-distance-blind-to-coverage.md`）。
5. **A/B 偏移朝相机方向**（不是沿法线），且组件比邻居多偏一档——
   见 `2026-09-23-normal-offset-sinks-behind-ghost.md`。
6. **完成标记是 `locator_marks.json`**（Blender 收尾才写），`locator.json` 只是"要渲哪些"
   的输入清单、由编排器先写——**不要拿它当完成判据**。

## 调参与重跑

全部视觉参数在 `config.toml [render.lookdev.locator]`（底色/幽灵色与不透明度/三色/
自发光强度/距离/`fit`/边距/偏移比例/共位容差），改完只需重渲定位图：

```bash
python -m meshq.pipeline render --target locator --keys <模型> --force
```

实测成本：p01（3 条）9.4 秒；p18（342 件 / 22 条）35 秒；**T2 全批（512 张单视图）约 10 分钟**。
几何不变即不用重渲（一次性产物）。`--target` 的图种级增量语义见
[`note-pipeline-incremental-rerun.md`](note-pipeline-incremental-rerun.md)。

**踩坑记录**：见 `2026-09-23-centroid-distance-blind-to-coverage.md`、
`2026-09-23-normal-offset-sinks-behind-ghost.md`、
`2026-09-23-import-and-weld-joins-whole-scene.md`、
`2026-09-23-win-argv-length-limit.md`。
- **关键词**：定位图, locator, 半透明, 三色拆分, 双机位, 标记环, 调参入口

