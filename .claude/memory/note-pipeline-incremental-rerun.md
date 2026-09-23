# note-pipeline-incremental-rerun — 各环节独立重跑可行性审计（2026-09-22，PLAN-05 P1~P3 依据）

> 结论先行：**网页文本层（05/06）已经是秒级独立重跑的纯函数**；**检出层没有任何
> 缓存**（全量约 18 分钟，改一个字的文案也要付）；**渲染层按模型整体跳过**但
> `--force` 一键绕过、且无通道级粒度。P1~P3 优化按此审计设计。

## 各环节审计（2026-09-22 现状）

| 环节 | 独立重跑 | 增量/跳过机制 | 重跑成本 | 备注 |
|---|---|---|---|---|
| 01 generate | ✅ | tasks 台账幂等（SUCCEEDED 跳过） | 按 credits | 付费，永远手动 |
| 02 render | ⚠️ 部分 | `outputs_complete` 按**模型整体**跳过；`--force` 绕过；通道组合硬编码（默认四通道/parts-only/pieces-only），**无图种级目标** | T2 ~8s/模型、standard ~3min/模型（1280×960，大头是 bpy 焊接与线壳几何操作，非出图） | P2 目标化对象 |
| 03 inspect | ✅ | 按 key 跳过 + `--keys/--force` | 秒~分 | |
| 04 features | ✅ | 按 key 存在即跳过 + `--keys+--force` 选择重算（注意 2026-09-21 覆写事故的修复语义） | 秒~分 | |
| 05 verdict 级联 | ✅ | 纯逻辑全量重算 | 秒 | 无需缓存 |
| 06 detect（detect） | ⚠️ 半 | 可 `--keys/--batch`，但**无 per-model 输入指纹**——岛层几何对未变化模型也全量重算 | 全量 ~18min（p11 227s、p18 358s） | 最大痛点；P1-B 方向 |
| 07 report（05） | ✅✅ | 数据产物纯函数，全量重生成 | ~10s | 文案模板在此层（finding_reason_text） |
| 08 deliver（06） | ✅✅ | 纯函数全量重生成 ~1min；转码暂无 mtime 跳过（P3） | ~1min | |
| 02b render `--target locator`（定位图） | ✅ | 图种级目标 + `outputs_complete`（标记 = `locator_marks.json`，Blender 收尾才写）；`--force` 全量 | T2 全批 **~10min**（512 张单视图）；单模型 p18（22 条）35s、p01（3 条）9.4s | 几何不变即不用重渲；视觉参数全在 `[render.lookdev.locator]`，见 [[note-locator-render-facts]] |

## 关键设计事实（改代码前必读）

1. **理由串已与几何计算分离**（2026-09-22，`finding_reason_text`）：检出只落
   简洁事实串 + 量化字段（overlap_frac/subtype/neighbor_rank/阈值）；白话展示文本
   由 `report.finding_reason_text(row)` 渲染时即时生成。**改措辞 = 改这一个
   函数 + 重跑 05/06（秒级），不需要重跑检出**。
2. **单件 GLB 已是持久化产物**：`render.export_review_pieces` 按 trimesh 划分
   导出待复核组件到 `render/pieces/<rank>.glb`——这是"拆分产物化"（P1 piece
   store）的既有雏形，P1 只是把范围从"待复核 rank"推广到"全部组件 + manifest 缓存"。
3. **`--parts-view` 是 regions/拼图/crops/单件渲染的唯一来源**：重渲不携带它会
   静默退化为无 regions 的三通道（2026-09-22 踩过并已让 pipeline render 透传）。
4. **陈旧产物风险**：`outputs_complete` 只看"文件存在"——旧渲染的
   highlight_regions 可能与当前级联/findings 严重不符（p18 曾只有 1 件）。图证类
   改动重渲必须 `--force`，或改用 pieces-only/pieces store。
5. **单引擎件（仅 trimesh 可见）在 bpy 渲染中不存在**：图证靠单件隔离渲染
   （`render_pieces_isolated`，trimesh 划分导出的单件 GLB 重演 lookdev 场景）。
   渲染时必须带线壳叠加 + Standard 观变换，否则平面件是一面"空墙"（2026-09-22
   审阅反馈）。

## 优化路线（已批准，2026-09-22 全部落地）

- ✅ **A 理由串分离**：`report.finding_reason_text`（展示层白话文本即时生成），
  检出器只落事实串 + 数字——文案迭代秒级生效（commit `24c8ae6`）
- ✅ **C `--pieces-only`**：单件隔离渲染独立执行，图证改动分钟级（同上）
- ✅ **P1 piece store**：`meshq/core/piece_store.py`（manifest 指纹失效重建），
  `export_review_pieces` 改读 store（commit `4ba97d9`）
- ✅ **P2 渲染目标化**：`--target overview|highlight|wire|parts|pieces|all` +
  图种级完整性 `target_complete`；旧旗标映射兼容（resolve_target）
- ✅ **P3 deliver 转码 mtime 跳过**

- **P1 piece store**：`data/raw/<key>/pieces/` 全组件单件 GLB + manifest（glb 指纹
  失效重建）；export_review_pieces 与未来消费者改读 store。注：features/detect 的
  in-memory 拆分暂不改造（T2 拆分 <1s，收益小、行为风险大），其重算去重归 P1-B
  （输入指纹）。
- **P2 渲染目标化**：`--target overview|highlight|wire|parts|pieces|all` +
  图种级完整性检查；旧旗标作为别名保留。
- **P3 deliver 转码 mtime 跳过** + 使用文档写明"网页层秒级独立重跑"。

## 更正（2026-09-23）：P3 的 mtime 跳过此前是**死代码**，交付层比审计结论更慢

审计结论"网页层秒级独立重跑"当时并不成立。两个原因，均已修复：

1. **`main()` 在重建前 `rmtree` 掉三类别目录的全部子目录**——先把产物删光，下游所有
   mtime 跳过（`_transcode`、逐检出条带）自然全部失效。**实测：只改一句文案也要 30 秒**，
   大头是 256 条逐检出条带的 PNG 解码重建。
   已改为**只删该删的**：范围外批次目录（standard 不进 T2 队列）与落位已变的旧类别
   （改判残留），未变化者原地保留。`_hstrip` 的条带生成同时补了 mtime 跳过。
2. **交付全量 copy 原始中间件**：`shutil.copytree(src, dest)` 连 `data/raw/<key>/render/`
   一起复制，实测占交付目录 **1953MB / 2082MB = 93.8%**，而交付页一张都不引用
   （页面只引 `images/` 下转码后的图）。已改为**按清单拷贝**：`model.glb` +
   `blender_checks.json` + `meta.json` + `pieces/`（逐件 GLB，PLAN-06 图例要按 rank 链到）
   + 转码进 `images/` 的图；原始件留在 `data/raw/`，交付目录可由本脚本整体重建。

**修复后实测**（T2 20 模型）：

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 交付目录体积 | 2082 MB | **133 MB** |
| 只改文案/页面结构的重跑 | 30 s | **2.4 s** |
| 冷启动全量重建（results 删空） | 49 s | 28.6 s |

**仍需注意**：交付侧清理现在也是"存在即跳过"语义，**改了渲染口径或模板必须确认是否真的
重跑了**——渲染侧用 `--force`；交付侧的跳过方向是"dest 不旧于 src 才跳过"，改模板不需
`--force`（页面每次重写），改图需要源文件更新才会重转。

相关：[[note-project-status]]、[[note-findings-contract-semantics]]、
[[note-report-readability-feedback]]、[[2026-09-23-deliver-crop-glob-mismatch]]
