# 交付快照里 20 份 pieces/manifest.json 带着开发机绝对路径

- **日期**：2026-09-23
- **现象**：`publish/<日期>/<类别>/<key>/pieces/manifest.json` 的每个 `glb` 字段都是
  `D:\Programs\2026-interview\MeshHomeWork\Mesh-Test\data\raw\<key>\pieces\<rank>.glb`
  ——盘符、上级目录名、本机目录结构全在里面，随快照一起入库分发。上一轮交接记的
  "0 处本机绝对路径泄漏"是只扫了 HTML 与 `data/` 附录、**没扫 `pieces/manifest.json`** 得出的。
- **根因**：`piece_store.ensure_store` 写 manifest 时用 `str(dest.resolve())`（绝对路径）。
  它在本机自用完全正确（trimesh 加载、传给 Blender 的 argv 都需要绝对路径），
  但这个文件**会随交付被复制出去**，于是"内部缓存文件"变成了对外泄漏面。
- **修复**（`a7b312e`）：
  1. manifest 内一律写**相对模型目录**的 `pieces/<rank>.glb`；
  2. 新增 `piece_glbs(manifest, key, data_dir)` 在需要绝对路径的两处（trimesh 加载、
     Blender argv/JSON）解析回来；
  3. `ensure_store` 命中缓存时**就地归一**老产物（不重拆分——重拆要跑几分钟），
     已在 data/ 下 41 份 manifest 上实测迁移完成；
  4. 单测覆盖：相对形式、解析、老绝对路径迁移后不重拆分、缺 `glbs` 映射被补回。
- **预防**：**"本机缓存"与"对外产物"共用同一份文件时，路径必须相对化**。
  交付前扫本机路径要以**整个快照目录**为范围（含 `pieces/`、`data/` 附录里的每个 JSON），
  而不是只扫页面；`git -c core.quotepath=false grep -l` 是这条检查的落地方式
  （见 `note-git-scan-quotepath`）。
- **关键词**：绝对路径, 泄漏, 交付快照, manifest, piece store, 相对路径, 扫描范围
