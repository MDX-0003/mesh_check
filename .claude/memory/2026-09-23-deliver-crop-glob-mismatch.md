# 交付页图证列全空：读取 glob 与写入文件名不一致

- **日期**：2026-09-23
- **现象**：`results/2026-09-22/` 全部 20 个模型页、**256/256 条待复核检出的"图证"列显示 `—`**，
  而对应的 `images/crop_<rank>.jpg` 文件**全部存在**（每页 `—` 的个数与该模型 `crop_*.jpg`
  文件数一一相等：p11 94/94、p06 42/42、p18 22/22…）。既有验收全绿：
  `tests/test_deliver.py` 只断言 `crop_3.jpg` **文件存在**，交付页 18 项机器验收里的
  "图证像素方差"检查的是**图片文件本身**（无高亮渲染的线框足以提供方差），两者都不看页面引用。
  审核台（现 `meshq/tools/review_server.py`，当时为 `review_serve.py`）在线裁决后重渲染走同一函数，因此在线看到的同样是空列。
- **根因**：`deliver.render_model_page` 的读取 glob 与 `deliver_one` 的写入名不一致——
  读 `crop_{rank}_*.jpg`（要求 rank 后有下划线），写 `crop_{rank}.jpg`。
  带下划线的通配永远匹配不到无后缀的文件名，故 `r["crop"]` 恒为 `None`。
  该 bug 自 `49f4348`（明细行内嵌逐检出图证）起一直存在，被"只验证文件、不验证引用"
  的断言掩盖。
- **修复**：`meshq/stages/deliver.py` 读取改为先取 `crop_{rank}.jpg`（写入名契约），
  保留 `crop_{rank}_*.jpg` 作为多图形态的向后兼容回退；
  `tests/test_deliver.py` 增加两条断言——页面必须出现 `src="images/crop_3.jpg"`、
  且不得再出现 `<td>—</td>`（回退该修复后新断言确实失败，验证断言有效）。
  重跑 deliver 后 256 条引用全部就位。
- **预防**：
  1. **产物命名的读写必须共用同一个构造函数/常量**，禁止一侧拼 `f"crop_{rank}.jpg"`、
     另一侧拼 glob 模式——两处独立拼接就是这类静默失效的温床；
  2. 交付层验收除"文件已生成"外，**必须断言"HTML 引用了它"**（引用断言的缺失是本次
     漏检的唯一原因）；
  3. "图证已有"类结论不得用像素方差间接证明，须以页面引用数为准（本次以
     `grep -c 'src="images/crop_'` = 256 复核）。

相关：[[note-pipeline-incremental-rerun]]、[[2026-09-21-stale-artifacts-break-render-regression]]
