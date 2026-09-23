# 项目状态备忘（快照）

- **日期**：2026-09-22（v2 检测管线实施完成当日）
- **要点**：
  - 批次与基线：standard 冻结归档（15/5/1，21 模型，405 credits，`report-standard.html` 不重算）；T2 主批 20/20 SUCCEEDED（95 credits，**余额 595**），判别级联基线 **A=0 / B=1322 / C=303**（A0 拦 277 = T2 普遍共位重复；C2=25、C1=1）；probe-poly15k 无 @preset 后缀、故意不进批次报告，**尚未进仓库外备份**（下次批末自动收）。
  - v2 已实施（commits `baddcd9..e4b81c3` + 分析 `0903341`，220 单测全绿）：findings 契约 + 检测关节 + 薄编排 + 报告 v2 版式；**两类检出只检出不删除**（用户拍板），04 --fix 未接入 v2。全批检出：T2 424 条（悬浮 122 / 共位 277 / 非核心 25）→ `report_t2.html`；standard 对照 9 条（1/5/3）→ `report-standard-v2.html`。
  - 关键翻案：p06@standard"真碎片"实为共位退化片（精确间隙=0，500 点采样高估造成浮岛假象）；p04 碎片真悬浮（精确 3.201%，余量仅 0.2pp）；T2 岛层新见 p06/p11 整组位移（组内贴合掩盖疏离，近邻级联不可见）与 p18 单件悬浮。距离核心 = 质心 KD 树 + 点-三角形（p06@standard 941s→1.9s），rtree 已非依赖。
  - 已实测约束（勿重查）：preview 单价 standard=20 / smart-topology=5 credits；任务回显 `model_type`/`seed` 不回显 `ai_model`，假 `ai_model` 被 400 拒；`target_polycount=15000` 实收 +7%；跨引擎组件匹配主键必须是绝对 diag（面数会被 bpy 焊接塌掉，p12 差 598 面）；gap 两口径（到主组件 vs 到最近任意件）差 30 倍不可混引；`--keys + --force` 曾整表覆写（已修，只有不带 --keys 的全量 --force 才从零重建）；既有产物未必与当前代码同源（像素回归用 `python -m meshq.tools.render_regression --golden`（当时文件名 `verify_render_identity.py`））。
  - 备份三层：任务成功即下载；批末自动 `run_backup` 快照 data/raw+tasks.json 到仓库外（jsonl 不在内，可从 GLB 重建）；task_id 免费补下载兜底。
  - 余下待办：①共位分型落地与否待用户拍板（见 bug 记录 2026-09-22-coloc-*）；②复核（悬浮组逐条目检、共位抽样 20~30、非核心 25）；③报告结论章节 + 两档对照表；④交付打包 + 推送远端。
