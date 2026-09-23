"""审核台：本地静态服务 + 裁决 API（PLAN-05 §三）。

    python -m meshq.tools.review_server results/2026-09-22 [--source data] [--port 8000]

- GET /<path>：静态文件（限定在 results 目录内，防目录穿越）；离线快照（直接
  file:// 打开）同样完整可读，页面控件 fetch 失败即降级只读。
- POST /api/disposition {key, disposition, reason}：
  1. upsert 唯一状态源 data/review.jsonl（人工裁决永不覆盖）；
  2. 重算落位 → 移动模型文件夹到新类别；
  3. 只重渲染受影响模型页 + 队列页（不重转码，亚秒级响应）。

不做鉴权、不加锁：单人本机使用；review.jsonl 原子写保证不读半截。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from meshq.core.review import (DISPOSITION_DIR, load_reviews, make_record,
                    plan_dispositions, upsert_review)

ROOT = Path(__file__).resolve().parents[2]   # 仓库根（模块现位于 meshq/<层>/<模块>.py）
STATIC_TYPES = {".html": "text/html; charset=utf-8",
                ".jpg": "image/jpeg", ".png": "image/png",
                ".json": "application/json",
                ".glb": "model/gltf-binary", ".js": "text/javascript"}


def make_handler(results_dir: Path, source_dir: Path):
    """构造绑定目录的 Handler 类（results/<日期>/ 与唯一状态源 data/）。"""
    import meshq.stages.deliver as deliver
    report_mod = deliver._load_report_mod()
    date_str = results_dir.name

    class ReviewHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):        # 安静：不刷屏
            pass

        # -------------------------------------------------- 静态
        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", ""):
                path = "/index.html"
            target = (results_dir / path.lstrip("/")).resolve()
            try:
                target.relative_to(results_dir.resolve())
            except ValueError:
                self._send(403, b"forbidden")
                return
            if not target.is_file():
                self._send(404, b"not found")
                return
            self._send(200, target.read_bytes(),
                       STATIC_TYPES.get(target.suffix, "application/octet-stream"))

        # -------------------------------------------------- 裁决
        def do_POST(self):
            if self.path != "/api/disposition":
                self._send(404, b"not found")
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                rec = make_record(body["key"], body["disposition"],
                                  reason=body.get("reason", ""))
            except (KeyError, ValueError, json.JSONDecodeError) as e:
                self._json(400, {"ok": False, "error": str(e)})
                return
            # 0) 先校验 key 属于本交付目录（**写入前**）——否则一个笔误的 key 会被写进状态源，
            #    并在队列页长出一张没有对应模型的"幽灵卡片"（2026-09-23 实测：20 → 21 张）。
            #    已知模型以三个类别目录为准（交付目录即权威范围）。
            known = set()
            for d in DISPOSITION_DIR.values():
                dd = results_dir / d
                if dd.is_dir():
                    known |= {m.name for m in dd.iterdir() if m.is_dir()}
            if rec["key"] not in known:
                self._json(400, {"ok": False,
                                 "error": f"未知模型 {rec['key']}：不在本交付目录内，裁决未写入"})
                return
            # 1) 唯一状态源落盘（人工裁决永不覆盖）
            upsert_review(source_dir / "review.jsonl", rec["key"],
                          rec["disposition"], reason=rec["reason"],
                          reviewed_by=rec["reviewed_by"])
            # 2) 重算全部模型落位（含各类别目录里的既有模型）
            rows = deliver.read_findings(source_dir / "findings.jsonl") \
                if (source_dir / "findings.jsonl").is_file() else []
            reviews = load_reviews(source_dir / "review.jsonl")
            keys = sorted(known | {rec["key"]})
            plan = plan_dispositions(rows, reviews, keys)
            # 3) 移动模型文件夹到新类别
            key = rec["key"]
            new_disp = plan[key]["disposition"]
            new_dir = results_dir / DISPOSITION_DIR[new_disp] / key
            old_dir = next((results_dir / d / key
                            for d in DISPOSITION_DIR.values()
                            if (results_dir / d / key).is_dir()), None)
            if old_dir and old_dir.resolve() != new_dir.resolve():
                new_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_dir), str(new_dir))
            # 4) 只重渲染受影响模型页 + 队列页（不重转码，亚秒级）
            if new_dir.is_dir():
                deliver.render_model_page(
                    key, new_dir, [r for r in rows if r["key"] == key],
                    {**plan[key], "reviewed_by": rec["reviewed_by"],
                     "reviewed_at": rec["reviewed_at"]}, report_mod)
            deliver.render_index(results_dir, date_str, keys, plan, rows)
            self._json(200, {"ok": True, "disposition": new_disp})

        def _send(self, code: int, body: bytes, ctype: str = "text/plain"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: dict):
            self._send(code, json.dumps(obj).encode("utf-8"),
                       "application/json")

    return ReviewHandler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="本地审核台：静态服务 + 裁决 API")
    ap.add_argument("results_dir", type=Path, help="results/<日期>/ 目录")
    ap.add_argument("--source", type=Path, default=ROOT / "data",
                    help="唯一状态源 data/（review.jsonl / findings.jsonl）")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)

    results_dir = args.results_dir.resolve()
    if not (results_dir / "index.html").is_file():
        print(f"缺少 {results_dir / 'index.html'}：先运行 "
              f"python -m meshq.stages.deliver --date {results_dir.name}")
        return 1
    handler = make_handler(results_dir, args.source.resolve())
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"[review] 审核台 http://127.0.0.1:{args.port}/"
          f"（results={results_dir}，Ctrl+C 退出）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[review] 已退出")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
