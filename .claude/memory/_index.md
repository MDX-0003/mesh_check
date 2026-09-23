# Bug 索引

| 日期 | 标题 | 一句话根因 | 关键词 |
|---|---|---|---|
| 2026-09-21 | trimesh.split needs graph engine | trimesh 图操作依赖 networkx/scipy，未装 | trimesh, split, networkx, 依赖 |
| 2026-09-21 | meshy users/me endpoint 404 | 账户端点不在已确认路径；列表端点才是验鉴权正解 | meshy, api, auth, 404 |
| 2026-09-21 | 测试污染真实数据 + GLB 完整性 | main 路径测试未隔离 DATA_DIR；下载只查存在不查完整 | 测试隔离, glb_valid, 完整性, 备份 |
| 2026-09-21 | Wireframe 修改器栈序颠倒致渲染 hang | 线壳先于 DECIMATE 求值，直接吃满百万面原网格 | blender, 修改器栈序, wireframe, decimate, 超时 |
| 2026-09-21 | 观变换 × 自发光强度的两个**反向**陷阱 | AgX 去饱和；反过来 Standard 下 `emission 1.0` 直接过曝成白（补记 2026-09-23，含 Blender 5.0 半透明三件套） | blender, AgX, view_transform, emission, 缺陷高亮, 过曝, 半透明 |
| 2026-09-21 | 既有产物被幂等跳过致回归出现假差异 | 幂等只看"文件存在"，旧产物从未被刷新；**同族第二例（2026-09-23）**：完成标记用了"跑之前就写"的产物 | 渲染回归, 幂等跳过, 陈旧产物, 像素比对, force 重渲, 完成标记 |
| 2026-09-21 | T2 报告可读性修复轮连环事故 | Blender 吞异常返回 0 / even_offset 炸壳 / embed 420 必糊 / --keys+--force 覆写 | blender, python-exit-code, wireframe, even_offset, embed, jsonl 覆写 |
| 2026-09-22 | 共位检出把拆件界面误报成 z-fighting 缺陷 | A0 证据（焊接消失）同因不同质：界面顶点网格共享 ≠ 重复曲面；overlap_frac 面积重叠率分型（0.2/0.6） | 共位, overlap_frac, 界面贴合, z-fighting, 分型, 误报 |
| 2026-09-22 | uv run trampoline 持续失败 | 仅 uv run 入口坏（lock/pip/venv python 正常）；Git Bash /tmp 与 Windows Python 不互通 | uv, trampoline, venv, shell, /tmp |
| 2026-09-23 | 交付页图证列全空：读取 glob 与写入名不一致 | 读 `crop_{rank}_*.jpg`、写 `crop_{rank}.jpg`，通配永远不匹配；只断言"文件存在"故漏检 | 交付, glob, 图证, crop, 引用断言, 静默失效 |
| 2026-09-23 | 单件图证从未把组件渲进画面 | 机位读 `bound_box` 而 `normalize()` 是就地改网格（缓存未更新）+ 绝对 `clip_start` → 相机放旧位置、组件在画外 | blender, bound_box, depsgraph, clip_start, 单件图证, 空图 |
| 2026-09-23 | `import_and_weld` 会 join 场景里全部网格 | 合并范围是"场景全部 MESH"而非"本次导入"，多对象场景被依次吞成一个；伴生：面数非跨阶段恒等量 | blender, import, join, 多对象场景, 面数口径 |
| 2026-09-23 | 面质心距离测不出"大面被小面盖住" | 单点采样只能测"小面落在大面上"；邻居 513 面全判未重叠 | 几何判据, 质心距离, 覆盖, 布尔拆分, 采样盲区 |
| 2026-09-23 | 沿法线微偏把组件推到幽灵之后 | 偏移符号取决于该件法线朝向，被半透明灰底混合冲淡；改为朝相机方向偏 | 半透明, 深度序, 法线偏移, 冲淡, EEVEE |
| 2026-09-23 | `world_background` 非幂等 | solid 分支只改节点默认值，残留 `ColorRamp→Color` 连线优先覆盖；渐变分支还堆积节点 | blender, world, node_tree, 幂等, 连线优先 |
| 2026-09-23 | Windows 命令行长度上限致整批中断 | 94 条清单塞进 argv → `WinError 206`，批处理在第 11 个模型处静默中断（日志 0 字节） | windows, argv, 命令行长度, 跨进程, 批处理 |
| 2026-09-23 | 产物集/命名一变旧产物不会自己消失 | 只生成不删除 + 消费侧用通配取图 → `images/` 留旧图种、条带被卷进 3 张陈旧图 | 产物集, 清理, 白名单, 通配取图, 陈旧残留 |
| 2026-09-23 | Blender 侧悬空导入让整个渲染链不可用 | 清理提交删了 `lookdev.assign_piece_materials` / `lookdev_math.legend_record`，`render_one.py` 的 import 没同步；bpy 脚本不在单测导入路径上，全绿也崩 | blender, 悬空导入, ImportError, render_one, 单测盲区 |
| 2026-09-23 | 逐检出合成图的 mtime 跳过吞掉 45 张陈旧图证 | 旧 `crops/` 链的产物与新一代条带**同名**（`crop_<rank>.jpg`）且 mtime 更新 → 普通重跑永不重建；`_spec_signature` 当时只管转码链 | 陈旧产物, 幂等跳过, 规格指纹, 图证, 命名沿用 |
| 2026-09-23 | 交付快照里 20 份 `pieces/manifest.json` 带本机绝对路径 | `str(dest.resolve())` 写进会随交付复制的 manifest；上轮"0 泄漏"只扫了页面与 data 附录 | 绝对路径, 泄漏, 快照, manifest, 相对路径 |
| 2026-09-23 | `_transcode` 跳过判据顺序反了：源缺失时先炸 | 短路只保护了 `dest.is_file()`，`src.stat()` 在源缺失时先抛 FileNotFoundError，后面那道守卫永远走不到；先判存在再读属性 | 增量跳过, 短路, stat, 源可选, 转码 |
| 2026-09-23 | `--clean-old` 白名单是手写常量 → 删掉 1345 张现役图 | 白名单没跟上 `piece_*`/`locator_*` 两族新产物，判据改为单一来源 `PRODUCT_PNG_PATTERNS` 并补三条单测 | 清理白名单, 产物集, 单一来源, clean-old, 数据损失 |

## 项目备忘（note-*，非 bug：设计决策 / 状态快照 / 用户反馈）

| 文件 | 内容 | 关键词 |
|---|---|---|
| note-project-status.md | 项目状态快照（批次/基线/credits/备份/已实测约束/待办） | 状态, 基线, credits, 备份 |
| note-deliverable-wording-redline.md | 对外交付文档严禁『测试』字眼，开发者口吻自查 | 措辞, 交付, 口吻 |
| note-plan-priorities.md | 判别优先、方法论可降级；特征提取 ≠ 判别 | 计划, 判别, 优先级 |
| note-findings-contract-semantics.md | findings 契约语义定论（tier 逐条、none 不落盘、A/B/C 对外退场） | findings, 契约, defect_class, tier |
| note-report-readability-feedback.md | 报告可读性五轮反馈与协作模式（分析→对齐→再改；修复授权下放） | 报告, 可读性, 反馈, 协作 |
| note-island-clustering-design.md | 视觉岛方法学依据（单连接/非破坏聚类/平台区）与采样高估警告 | 岛层, 聚类, epsilon, 方法学 |
| note-pipeline-incremental-rerun.md | 各环节独立重跑可行性审计（渲染/检出无粒度缓存是痛点）+ P1~P3 优化路线与必读设计事实 | 增量, 重跑, 渲染目标, piece store, dry-run |
| note-long-task-execution.md | 长任务执行约定：后台进程活不过工具调用 → 前台分块 + 幂等续跑 + 用产物计数看进度 | 长任务, 后台进程, 幂等续跑, 产物计数 |
| note-screenshot-verification.md | 截图验证约定：先断言 DOM 再截图（宽视口会出现重复列/陈旧帧） | 浏览器, 截图, DOM 断言, 验证方法 |
| note-git-scan-quotepath.md | 扫仓库一律 `-c core.quotepath=false`（否则中文名文件被静默跳过） | git, quotepath, 中文名, 静默跳过, 覆盖面 |
| note-locator-render-facts.md | 定位图（`--target locator`）的渲染事实与调参入口：半透明灰底让遮挡不再是约束、双机位、标记环、完成标记 | 定位图, locator, 三色拆分, 双机位, 调参入口 |
| note-render-delivery-checklist.md | 渲染/交付层自查清单（产物命名 / 跳过判据 / 跨引擎契约 / 观变换 / 半透明 / 先看后批） | 自查清单, 产物命名, 跳过判据, 跨引擎契约, 先看不批 |

