# 测试污染真实数据：main 路径测试未隔离 DATA_DIR，假下载覆盖真实模型

- **日期**：2026-09-21
- **现象**：M1 后处理链在删减式修复路径（当时的 `04_detect --fix`，该路径已于 2026-09-23 从代码库移除，见 PLAN-08 §1.3）读 GLB 时崩溃（`buffer size must be a multiple of element size`）；排查发现 `p01@standard` / `p01@smart-topology` 的真实模型被 14 字节假文件覆盖。备份同样在污染之后执行，备份里也是假文件。
- **根因**：`test_main_invokes_backup_after_batch` 走 `main()` → `run_batch()` 全路径，但只 patch 了台账/配置/client，没有 patch `DATA_DIR`——`run_batch` 的 `raw_dir` 默认参数落到真实 `data/raw`，`ScriptedClient.download` 把假字节写进了真实模型文件。连带暴露第二层问题：下载落盘只检查"文件存在"，不检查内容，假文件/截断文件都畅通无阻。
- **修复**：
  1. 该测试补 `monkeypatch.setattr(gen, "DATA_DIR", tmp_path)`；
  2. `common.glb_valid()`：校验 GLB magic + 头部声明长度 == 实际大小；
  3. `generate` 的补下载触发条件从"文件缺失"升级为"缺失或无效"，`_store_outputs` 后再校验；`ScriptedClient.download` 改写结构合法的 GLB；
  4. 新增 `--verify` 子命令一键校验台账内全部本地模型。
- **预防**：任何走 `main()` 全路径的测试必须隔离 `DATA_DIR`（连同 `TASKS_FILE` 等模块级路径）；下载类操作必须带完整性校验（头部字段比对），"存在 ≠ 完整"；涉及付费资产的流水线，批处理结束先 `--verify` 再备份。
- **恢复记录**：两个被污染模型经 `--smoke` 幂等补下载恢复（API 端模型 URL 仍有效，零 credit）。
