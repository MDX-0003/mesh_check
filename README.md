# Mesh-Test — 生成式 3D 资产几何质检管线

对 Meshy T2 API 批量生成的 GLB 模型做自动化几何质检：

**交付物 = 代码（本仓库）+ 模型及渲染图与交付说明（都在 `publish/<日期>/` 快照内）。**

## 目录导览

| 目录 | 是什么 | 随仓库分发 |
|---|---|---|
| `meshq/` | 全部代码（`core` 纯逻辑 / `stages` 六段 / `tools` 工具 / `blender` 只在 Blender 内置 Python 里运行） | ✅ |
| `publish/<日期>/` | **离线只读快照**：图证 + 每模型 GLB + 明细页 + 交付说明，双击即读 | ✅ |
| `.claude/memory/` | 现实 bug 库与设计备忘（动手前先查 `_index.md`） | ✅ |
| `data/` | 管线产物（模型 / 渲染中间件 / 指标 / 检出 / 台账） | ❌ 体积大、可由代码重建，用 `meshq/tools/seed_data.py` 装载 |
| `results/<日期>/` | 本机交付工作目录（含可裁决的服务端形态） | ❌ 可由 `meshq/stages/deliver.py` 从 `data/` 重建 |

## 怎么跑起来（概览）

完整步骤与数据包说明见 **[`publish/2026-09-22/交付说明.md`](publish/2026-09-22/交付说明.md) 第五节**，这里只给最短路径：

```bash
uv sync                                  # 还原虚拟环境（python 3.12）

# A 只读结果：双击 publish/2026-09-22/index.html（不需要 Python / data/）

# B 在线裁决（只需代码 + 快照）
python -m meshq.tools.review_server publish/2026-09-22 --source publish/2026-09-22/data

# C 重跑分析与交付（需要 data/）
cp config.example.toml config.toml               # 分析/报告阶段要读它（Blender 路径与 key 可留空）
python -m meshq.tools.seed_data --check          # 体检：缺什么
python -m meshq.tools.seed_data --unpack <数据包目录>   # 解压到仓库根（包内即仓库相对路径）
python -m meshq.tools.seed_data --check          # 再体检：全 OK 即装载成功
python -m meshq.pipeline detect && python -m meshq.pipeline report
python -m meshq.pipeline deliver --date 2026-09-22

# D 本机没有模型：重新生成（付费，先干跑看预算）
python -m meshq.pipeline generate --dry-run
python -m meshq.stages.generate --all --preset smart-topology
```

数据产物在 `data/`（不入库）；原始模型自动备份到 `[backup] dir`（`config.toml`），代码有 bug 时直接复用本地模型重跑分析，不重复消耗 credit。**只有 `render` 阶段需要 Blender**（`config.toml` 的 `[blender] path`），其余步骤纯 Python。

## 代码结构

```
meshq/
  pipeline.py          统一入口（stage 编排 + 全阶段 --dry-run）
  core/                纯逻辑与契约（不依赖 bpy，也不依赖"阶段"概念）
    common.py            配置加载、glb_valid 完整性校验、MeshyClient、TaskLedger（幂等台账）
    geometry.py          纯逻辑（边分类 / 主组件 / 规模口径 / 三档判定），两侧引擎共用
    mesh_ops.py          trimesh 侧几何操作（焊接 / 连通域 / 组件特征 / 包围盒贴片判定）
    findings.py          检出结果契约（findings.jsonl = 报告与交付的唯一检出输入）
    detectors.py         检测关节 + REGISTRY（岛层悬浮 / 共位双证据 + 面积重叠率分型）
    part_features.py     部件特征提取 → parts.jsonl
    part_verdict.py      部件判别级联 → part_verdicts.jsonl
    piece_store.py       拆分产物化：data/raw/<key>/pieces/<rank>.glb + manifest.json
    locator.py           定位图视角选择（纯投影打分，确定性、可单测）
    lookdev_math.py      色彩空间 / 灯位数学 / 视图与机位常量（纯函数，两侧共用）
    review.py            裁决契约与落位规则（唯一状态源 data/review.jsonl）
  stages/              管线六段（每段有自己的 main()，可单独执行）
    generate.py          生成（幂等：SUCCEEDED 跳过 / PENDING 续查 / 坏文件补下载）
    render.py            Blender 编排器 + 后处理（线框 SSAA 降回、前后对比图、图种级 --target）
    metrics.py           trimesh 独立复核 → metrics.jsonl（边口径已拆：boundary / non_manifold）
    detect.py            v2 检出编排 → findings.jsonl（检出关节在 core/detectors.py）
    report.py            Jinja2 单文件 HTML（报告为本地分析件，不在交付内）
    deliver.py           交付目录 results/ + 离线快照 publish/（HTML 模板与图片转码都在这）
  blender/             只在 Blender 内置 Python 中运行（见该目录 __init__.py）
    render_one.py        单模型渲染入口（通道编排 + 产物契约）
    lookdev.py           世界背景 / 灯光 / 材质 / 相机族 / 渲染
    bpy_geom.py          导入焊接 / 连通域 / 几何计数 / 归一化 / 碎片像素投影
  tools/               按需运行的辅助工具（不属于六段常规流程）
    backup.py            模型备份（仓库外）
    review_server.py     本地审核台（静态服务 + 裁决 API）
    seed_data.py         数据装载与分发（体检 / 打包 / 解包校验）
    render_regression.py 渲染产物像素零差异回归（排查用）
tests/                 镜像 meshq/ 结构（纯逻辑全覆盖；bpy 与网络用假件隔离）
```

**依赖方向单向**：`meshq/blender/*` 与 `meshq/stages/*` 都可以 import `meshq/core/*`，反之不行
（`blender/` 层只在 Blender 内置 Python 里运行）。

完整流程：**批量生成 → Blender headless 渲染（lookdev 展示场景）→ 双引擎几何指标（bpy 原生 + trimesh 复核）→
v2 检测管线：一切服务「不应该存在的组件」检出——视觉岛层悬浮检出 + 共位双证据检出 + 面积重叠率分型，
只检出不删除 → 分析报告（单文件 HTML）+ 交付目录（确定通过 / 确定不通过 / 待人工裁决）+ 本地审核台 +
离线只读快照。**

## 当前检测与交付架构

- **检测**：`meshq/stages/detect.py` 是薄编排，检测关节注册在 `meshq/core/detectors.py`，结果统一落
  `data/findings.jsonl`（契约见 `meshq/core/findings.py`）。两类核心缺陷（悬浮 / 重叠共面）逐条带证据与
  白话理由，**只检出不删除**；界面贴合（拆件正常形态）计为导出特性度量，不占复核队列。
- **分析报告**：`meshq/stages/report.py`——检出汇总首屏 + 按缺陷类分组复核表（无 findings 的批次保持 legacy 版式）。
- **交付**：`meshq/stages/deliver.py` 生成 `results/<日期>/`（确定通过 / 确定不通过 / 待人工裁决，按清单拷贝 +
  图片按目的分辨率转码）；`meshq/tools/review_server.py` 在线时可页面内裁决，离线为只读快照。
- **统一入口**：`python -m meshq.pipeline <stage> [--dry-run]`（generate / render / inspect / detect /
  report / deliver / all；付费段与重渲段不入 all）。
- **组件定位**（PLAN-07）：`meshq/core/locator.py`（纯逻辑选视角）+ `render_locator_scene`
  （`meshq/blender/render_one.py` 内出图），逐检出落 `render/locator_<rank>_{model,close}.png` 与
  `locator.json`，交付层横拼成一张并画标记环。

## 离线快照（`publish/`）

`publish/<日期>/` 是可直接分发的**离线只读网页快照**：clone 本仓库后无需任何服务，
双击 `index.html` 即可阅读全部模型页与图证（裁决控件自动降级为只读）。

```bash
python -m meshq.stages.deliver --date <日期> --out publish --snapshot
```

- 只包含页面真正引用的东西：转码后的图、每模型 GLB、判定记录、附录 JSONL、交付说明；
  不含 `data/raw/` 里的渲染中间件（那部分可由本脚本从 `data/` 重建）。
- 相对引用 + 无本机绝对路径，故可整目录拷走／压缩分发。
- 在线裁决在本机运行审核台：`python -m meshq.tools.review_server publish/<日期> --source publish/<日期>/data`
  （**只靠快照即可裁决**，不需要 `data/`）。

## 结果速览（standard 对照批，21 个模型）

**漏斗：15 直接入库 / 5 修复后入库 / 1 拒绝。成本 405 credits**

| 任务书四问 | 结论 |
|---|---|
| prompt 怎么选 | 16 个多类别日常物（覆盖硬表面/有机/细柄薄壁/对称形态谱系）+ 4 个悬浮体诱导（热气球/吊灯/举物角色/风铃）。见 `prompts.jsonl` |
| 评估结论 | standard 档（meshy-7）几何质量高：15/20 单一连通组件、非流形边 0、无退化几何；面数 40 万~175 万。悬浮组件是真实存在的主要缺陷（见下） |
| 为什么自动检测悬浮组件 | Meshy 官方插件为此专门提供检测+删除 UI（自述参照 Houdini Labs Delete Small）；它是文献公认的头号生成伪影；下游入库对它零容忍；算法全代码可复现 |
| 效果如何 | 6 个问题模型全部正确识别并分档：5 个删减式修复后入库（保住真部件、删掉碎屑），1 个因双引擎判定冲突转人工（拒绝档）。代表性案例见下 |

### 代表性案例

- **p13 章鱼**：触手间 7,108 面悬浮碎片（占总面数 1.34%）→ 检出、红色高亮、删除断开引用，主体无损（530,426→523,318 面）
- **p18 吊灯**：24 个组件中 4 个碎屑（23,440 面）被删，**20 个垂挂水晶真部件全部保留**——相对阈值（主组件对角线 10%）跨类别免调参的直接证据
- **p06 球鞋**："一双鞋"两只等大部件（对角线比 0.92）安全通过阈值，仅清除 2 处微碎屑
- **p12 手柄**：一个 65,848 面的密集部件与主体仅轻微接触——trimesh（1e-5 焊接）判分离、Blender（1e-4）判连通。**双引擎冲突 → 拒绝档转人工**，而非冒险自动删除

### 已知边界

- 高亮图以 Blender 引擎的检出为准：仅接触式连接（焊接后合流）的部件，Blender 侧不可见，如 p12；空间分离的碎片（主流 floaters 形态）两引擎一致
- 定位图的整机面板对极小件偏弱（组件可小到模型对角线 0.4%）：标记环保证"找得到"，但环内可能只有几像素；该件的实际形态以**逐组件独立渲染**（三视图隔离渲染）为准
- 诱导 prompt 不必然诱导成功：p17 热气球、p19 举物角色被生成器整体融合为单一组件（检测器正确输出"无碎片"，与网格事实一致）
- 碎片面率（面数占比）与尺寸占比是两个维度：p12 的小部件面数占 9.3% 但对角线只占 7.8%，拒绝档用的是面率，偏保守
