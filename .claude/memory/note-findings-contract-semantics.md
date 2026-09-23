# findings 契约语义（与用户逐条确认过的定论）

- **日期**：2026-09-21 侧聊确认；已按此实现于 `meshq/core/findings.py`（commit `baddcd9`）
- **要点**：
  1. **记录单位是 finding（检出条目），不是模型**：一条 finding = 某模型的一个组件（或一组，C1 整组位移用 ranks 数组）+ 一种 defect_class + 一个 tier。schema：`{key, detector, defect_class: floating|co_located|other|none, tier: review|keep（auto 为保留值，v2 契约层拒绝写入）, ranks, evidence{}, reason, metrics}`。
  2. **tier 不是模型属性**：同一模型不同组件可携带不同 (defect_class, tier)；模型级只有聚合视图（报告检出汇总 = 按 defect_class 现算 N 条/M 模型），同一模型可同时计入悬浮与共位两个口径。
  3. **none 不落 findings.jsonl**：枚举含 none 仅为映射完备（B/本体）；检出清单只落 floating/co_located/other，keep 语义留在 part_verdicts.jsonl 作对照。
  4. **非核心（other）= C2 残余 + A1-off**：真缺陷（p13 缝隙型）但不属于两类核心叙事——检出不丢、汇报单列、不进核心计数、人工低优先。
  5. **A/B/C 代号从交付物退场**：级联保留为内部分诊映射与回归锚点（T2 基线 A=0/B=1322/C=303），编号只留在 entry 字段追溯；报告/文档用 floating/co_located/other 白话分组（用户抱怨代号可读性差后的修复方向，见 note-report-readability-feedback 第四轮）。
- **实现提醒**：别把 tier 提升到模型级，别把 none 写进检出清单，别让 A/B/C 出现在对外文案。

## 追加（2026-09-23）：`engine_bpy_rank` / `engine_seen_by_bpy` 的真实语义

**改渲染档位映射或证据措辞前必读。** 这两个字段由 `part_features.match_bpy_partition`
（`part_features.py:101`）产出，判据是**几何相似度**，不是拓扑包含：

```
diag 相对差 <= ENGINE_DIAG_TOL  且  面数差 <= max(ENGINE_FACE_TOL, rel × 面数)
```

因此把每个 trimesh 件拿去和**所有** bpy 件比对，取第一个落入容差的。

- **`engine_seen_by_bpy == False` ≠ "bpy 看不见它"**，更 ≠ "因顶点重合被并入本体"。
  它只表示"**没有尺寸与面数都相近的 bpy 件**"。真实原因至少有三种：被焊进主体、
  被焊进邻件（于是 bpy 那件更大/更小，双双超容差）、两引擎对"哪件最大"判断不同。
- **失败规模远超预期**（实测）：p15 144 件中 40 件无对应（**含 4802 面的最大件**）、
  p18 342 件中 99 件无对应、p01 11 件中 3 件无对应。也存在**一对多**（bpy 一件 ← 多个
  trimesh 件，如 p01 bpy rank 5 ← 2 件）。
- 页面证据串把它渲染成"bpy 视角下并入本体（顶点重合）"属**过度解读**，待修正措辞
  （PLAN-06 §四；只改 `report.evidence_str`，不动检出逻辑）。
- **推论**：`build_tiers` 依赖这条转接，故当前有相当比例的检出在渲染侧**根本无法高亮**
  （p01 0/3、p15 6/31、p18 1/22）。换成 trimesh 划分做渲染后该映射退化为恒等映射，
  限制随之消失——论据与实测见 PLAN-06 §2.3。
