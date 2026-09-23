# Windows shell 与 uv 入口的坑：trampoline 持续失败用 venv python 直跑；/tmp 不跨 Git Bash 与 Windows Python

- **日期**：2026-09-22
- **现象**：①`uv run pytest` 报 `uv trampoline failed to canonicalize script path`，**连续多次不自愈**（与 2026-09-21 曾自愈的一次不同）；②Bash 工具层偶发瞬时包装层故障（所有命令含 `echo` 被坏包装拦截，报 `C:\Python314\python.exe: can't open file "...\$0'"`），重试即愈；③Git Bash 写 `/tmp/x.log`，Windows Python 读不到（两套 /tmp 不互通）；④长任务经管道 `| grep | head` 会丢输出尾部且 exit code 取最后一个命令。
- **根因**：①`uv run` 入口的 trampoline 坏而 `uv lock`/`uv pip`/`.venv\Scripts\python.exe`（本身也是 uv trampoline）都正常——仅 `uv run` 这一个入口的问题，不必恋战排查；②③④是 Git Bash 与 Windows 双环境的标准错位。
- **修复**：统一改用 `.venv/Scripts/python.exe -m pytest -q` / `-m xxx` 直跑（本会话全程如此，无碍）；跨进程交换文件用仓库内路径；长任务输出落文件再查（`> /tmp/x.log 2>&1` 仅供 bash 侧消费）。
- **预防**：①`uv run` 报 trampoline 错直接换 venv python，不重试超过一次；②临时挂未装依赖用 `uv run --with rtree --with scipy`（不动 pyproject/uv.lock）——注意 2026-09-22 起岛层精确距离改用质心 KD 树 + 点-三角形自实现，**rtree 已不是本项目依赖**，无需再挂；③跑完用 `git status --short uv.lock pyproject.toml` 确认锁文件未被临时脚本污染。
