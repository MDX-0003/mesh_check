# 扫仓库一律加 `-c core.quotepath=false`

- **日期**：2026-09-23
- **事实**：本仓库有大量**中文文件名**（如 `publish/2026-09-22/交付说明.md` 等）。
  `git ls-files` 默认 `core.quotepath=true`，会把非 ASCII 路径输出成**八进制转义**
  （`"publish/2026-09-22/\344\272\244..."`），于是

  ```bash
  git ls-files | while read f; do [ -f "$f" ] || continue; grep ... "$f"; done
  ```

  这类逐文件扫描会**静默跳过全部中文名文件**。实测：一次"逐词自查对外措辞"的扫描因此漏掉
  **42 个**中文名文件，差点给出"已清理干净"的错误结论。
- **约定**：
  1. 扫仓库内容（敏感词、措辞红线、引用检查）一律用
     `git -c core.quotepath=false ls-files`；
  2. 逐文件循环里对 `[ -f ]` 失败**不要静默 `continue`**，至少计数并在结尾比对
     "扫到的文件数 vs `ls-files` 总数"，不一致就报出来；
  3. 与 `/tmp` 那条同理（`2026-09-22-uv-run-trampoline…`）：**工具链默认值会静默丢文件**，
     结论前先验证"我确实扫到了全部文件"。
- **关键词**：git, quotepath, 中文文件名, 静默跳过, 敏感词扫描, 验证覆盖面

