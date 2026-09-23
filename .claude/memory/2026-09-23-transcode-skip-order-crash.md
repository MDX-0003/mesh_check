# `_transcode` 的跳过判据顺序反了：源缺失时先炸，而不是按承诺返回 False

- **日期**：2026-09-23
- **现象**：交付目录里已有图、而某个渲染源不存在时，`deliver` 当场
  `FileNotFoundError: [.../render/base_iso.png]`，整批中断；而函数的 docstring 承诺
  "源缺失返回 False（调用方保留既有图 / 页面留占位）"。
- **根因**：跳过判据写成
  `if not force and dest.is_file() and dest.stat().st_mtime >= src.stat().st_mtime: return True`
  再才 `if not src.is_file(): return False`。短路只能保护 `dest.is_file()` 为假的情况；
  一旦目标已存在（正常增量场景）且源缺失，`src.stat()` 先抛异常，后面那道守卫**永远走不到**。
- **触发场景很现实**：`data/raw/<key>/render/` 是中间件，清理或误删都可能动到它；
  只要删到的包含 `base_iso.png`（供 `thumb.jpg` / `base.jpg`）或 `wire_*.png`（本就原样转码），
  下一次 `deliver` 必崩。**新增图种或改转码清单时，这类"源可选"的假设会更容易踩到。**
- **修复**：把 `src.is_file()` 提到 mtime 比较之前（先查源、再比新旧），并补三条回归——
  单元级（目标已存在 + 源缺失 → 返回 False 且既有图保留）、无目标 + 源缺失、以及端到端
  （交付过一次 → 删 `base_iso.png` → 再次交付必须 rc=0）。**反向验证过**：还原旧顺序即复现
  `FileNotFoundError`。
- **预防**：凡是"跳过/增量"判据，**先判存在、再读属性**——`stat()`、`open()`、`read_bytes()`
  都属"读到才算存在"的操作，不能参与短路链的前半段。同族判据（定位图 / 单件条带两处）
  早先用 `max(..., default=0)` 修过，本条是漏网的第三处。
- **关键词**：增量跳过, 短路求值, stat, FileNotFoundError, 源可选, 交付转码
