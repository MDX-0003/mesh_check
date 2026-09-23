# CLAUDE.md — 本仓库开发规范

任何 Agent（Claude Code / ZCode / 其他）在本仓库工作前必须先读本文件、`README.md`
与 `.claude/memory/_index.md`。本仓库是对外共享的工程仓库：所有文档、注释、commit message
一律以**开发者完成需求的口吻**书写，只说"要做什么、实现什么、怎么实现"。

## 1. 项目是什么

生成式 3D 资产几何质检管线：Meshy T2 API 批量生成 GLB → Blender headless 渲染 →
双引擎几何指标（bpy 原生 + trimesh 复核）→ v2 检测管线（视觉岛层悬浮检出 + 共位双证据检出 +
面积重叠率分型，只检出不删除）→ 分析报告（单文件 HTML）+ results 交付目录与本地审核台 +
`publish/` 离线只读快照。模块地图见 `README.md`「代码结构」。

## 2. 环境与配置

- **uv 管理虚拟环境**：`uv sync` 还原，`uv run python …` 执行，`uv run pytest` 跑测试。
  不使用本机 Python，不用裸 pip。
- **config.toml 是设备相关配置的唯一来源**（blender exe 路径、API key、检测阈值、渲染参数、
  轮询间隔）。脚本内禁止硬编码这些值；新增同类配置一律进 config（先改 `config.example.toml`
  模板，再改 `common.py` 加载器）。
- `config.toml` 含 API key，**已在 .gitignore 排除，任何情况下不得提交**。
- Blender 侧脚本（`meshq/blender/`）只经 `<config.blender.path> --background --python …` 入口执行；
  Blender 内置 Python 与 uv 虚拟环境物理隔离，不得混用。依赖方向单向：`meshq/blender/` 可以
  import `meshq/core/` 的纯逻辑模块（`lookdev_math` / `geometry`），反之不行。
  **包内引用一律 `from meshq.<层>.<模块> import …`**；这条边界由
  `tests/test_package_imports.py` 静态守着（AST 扫 import 目标 + 直接执行脚本必须有
  `sys.path` 引导），因为 Blender 侧不在单测导入路径上——`pytest` 全绿也可能渲染直接崩。
- **`data/` 与 `results/` 不入库**（体积大、可由代码重建）。换机器时用
  `python -m meshq.tools.seed_data --check` 体检、`--unpack <包>` 装载；`publish/` 是入库的交付快照。

## 3. 开发规范

1. **单测必配**：每个功能模块在 `tests/` 有对应测试（镜像 `meshq/` 结构：`test_generate.py`、
   `test_detect.py`…）。纯逻辑（阈值判定、台账幂等、配置加载、JSONL 读写）必须全覆盖；
   网络与 bpy I/O 用假件（fixture/monkeypatch）隔离，或显式标记 `@pytest.mark.integration` 不进 CI。
2. **CI/CD**：`.github/workflows/ci.yml` 在 push / PR 时自动跑 `uv sync` + `uv run pytest`。
   **push 前本地测试必须先绿**；CI 红了优先修 CI。
3. **小步提交**：每完成一个最小可用功能单元，立即本地 commit，不攒批。message 格式：
   `<type>: 中文一句话`，type ∈ `feat / fix / test / docs / refactor / chore`。
   里程碑完成点单独 commit 并在 message 里注明。
4. **Memory 纪律**：开发过程中遇到的**每一个现实 bug**（真实发生、真实排障的），修复当天必须
   录入 `.claude/memory/`（格式见该目录 README）；动手修同类问题前先查 memory。假设性、
   未复现的问题不录。
5. **数据产物规范**：指标/判定一律 JSONL（每条记录自描述键名），全局汇总 `summary.json`；
   不新增 CSV。`data/` 整体 gitignore，不随仓库分发。
6. **文档口吻**：对外可见的文档（README / 交付快照里的 `交付说明.md`）保持工程视角；引用时间戳、阈值、
   credits 等数字必须来自产物文件，不手编。
7. **渲染/交付层改动：先渲一个样本、看一眼，再批量**。新通道、新图种、新机位首次落地时
   **禁止直接跑全批**；样本挑最坏情况（件数最多 / 组件最小 / 参数最极端）。理由：这一层的
   故障几乎都是"产物形状对、但内容不对"（空图、配色被冲淡、拼图多扛了几张旧图），单测与
   "文件存在"类断言拦不住——动手前先扫 `.claude/memory/note-render-delivery-checklist.md`。
8. **删东西要删干净**（2026-09-23 补，两条都真出过事）：撤一个产物/函数时，同时 grep 它的
   引用点（含 Blender 侧脚本的 import 与白名单常量）；**清理白名单必须与产物集同源**——
   `render.py` 的 `PRODUCT_PNG_PATTERNS` 是唯一来源，加新图种必须在那里登记，
   否则 `--clean-old` 会把新产物当旧残留删掉。

## 4. 参考资料导航

| 路径 | 内容 |
|---|---|
| `README.md` | 项目概览、代码结构、运行方式、historical baseline 与已知边界 |
| `publish/<日期>/` | 离线只读快照（入库的交付形态，双击即读）；交付说明的唯一维护位置在快照根目录 |
| `.claude/memory/` | 现实 bug 库（`_index.md` 索引 + `note-*` 备忘） |
