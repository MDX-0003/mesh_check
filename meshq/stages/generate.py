"""01 · 批量生成（幂等）。

用法：
  uv run python -m meshq.stages.generate --smoke          # 1 条 prompt × 2 档位（M0）
  uv run python -m meshq.stages.generate --all            # 20 条全量（M1，--preset 选档）
  uv run python -m meshq.stages.generate --status         # 只看台账汇总

幂等语义（data/tasks.json）：
  SUCCEEDED → 跳过；PENDING/IN_PROGRESS → 续查；无记录/FAILED/CANCELED → 重建任务。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

from meshq.core.common import (
    DATA_DIR,
    MODEL_PRESETS,
    ROOT,
    ConfigError,
    MeshyClient,
    TaskLedger,
    build_task_payload,
    get_api_key,
    glb_valid,
    load_config,
    load_prompts,
)
from meshq.tools.backup import run_backup


def echo_fields(preset: str, task: dict) -> dict:
    """任务回显字段落台账（PLAN-03 §7.2）："是 T2"的举证链。

    `ai_model` 不回显（官方行为，已实测），"确实是 T2"由 model_type 回显 + 价档 + 面数
    三条指纹间接证明，其中本函数负责把能落库的都落库。
    """
    return {"model_type_echo": task.get("model_type"),
            "seed": task.get("seed"),
            "target_polycount": MODEL_PRESETS[preset].get("target_polycount")}


def poll_task(client: MeshyClient, task_id: str, interval_s: float, timeout_s: float,
              log=print) -> dict:
    """轮询至终态；网络类错误退避重试；超时返回 status=TIMEOUT（由调用方处置）。"""
    t0 = time.monotonic()
    while True:
        try:
            task = client.get_task(task_id)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            if code in (429, 500, 502, 503, 504):
                time.sleep(interval_s * 3)
                continue
            raise
        status = task.get("status")
        if status == "SUCCEEDED" or status in client.TERMINAL_BAD:
            return task
        if time.monotonic() - t0 > timeout_s:
            log(f"  [timeout] {task_id} 超过 {timeout_s:.0f}s")
            return {**task, "status": "TIMEOUT"}
        time.sleep(interval_s)


def plan_smoke(prompts: dict) -> list[tuple[str, dict, str]]:
    """冒烟：1 条 prompt × 2 档位。"""
    pid = sorted(prompts)[0]
    return [(f"{pid}@{preset}", prompts[pid], preset)
            for preset in ("standard", "smart-topology")]


def plan_all(prompts: dict, preset: str) -> list[tuple[str, dict, str]]:
    return [(f"{pid}@{preset}", rec, preset) for pid, rec in sorted(prompts.items())]


def _store_outputs(client: MeshyClient, key: str, rec: dict, preset: str,
                   task: dict, raw_dir: Path) -> dict:
    """SUCCEEDED 任务的产物落盘：GLB + 缩略图 + meta.json。补下载路径复用。"""
    out_dir = raw_dir / key
    glb_url = (task.get("model_urls") or {}).get("glb")
    if glb_url:
        client.download(glb_url, out_dir / "model.glb")
    thumb_url = task.get("thumbnail_url")
    if thumb_url:
        client.download(thumb_url, out_dir / "thumbnail.png")
    meta = {
        "key": key, "pid": rec["pid"], "preset": preset, "prompt": rec["prompt"],
        "category": rec.get("category"), "expect_floater": rec.get("expect_floater"),
        "task_id": task.get("id"), "status": task.get("status"),
        "credits": task.get("consumed_credits"),
        "has_glb": bool(glb_url),
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return meta


def run_batch(plan: list[tuple[str, dict, str]], cfg: dict, client: MeshyClient,
              ledger: TaskLedger, log=print, raw_dir: Path | None = None) -> dict[str, dict]:
    poll_interval = float(cfg["poll"]["interval_seconds"])
    poll_timeout = float(cfg["poll"]["timeout_seconds"])
    raw_dir = raw_dir or (DATA_DIR / "raw")
    results: dict[str, dict] = {}

    for key, rec, preset in plan:
        if not ledger.needs_task(key):
            done = ledger.get(key)
            glb_path = raw_dir / key / "model.glb"
            if (done.get("status") == "SUCCEEDED" and done.get("task_id")
                    and not glb_valid(glb_path)):
                # 崩溃安全：任务已成功但本地文件缺失或损坏（下载中断/截断）
                # → 按台账 task_id 补下载，绝不重新生成
                task = client.get_task(done["task_id"])
                if (task.get("model_urls") or {}).get("glb"):
                    meta = _store_outputs(client, key, rec, preset, task, raw_dir)
                    if glb_valid(raw_dir / key / "model.glb"):
                        ledger.update(key, credits=task.get("consumed_credits"),
                                      **echo_fields(preset, task))
                        log(f"[backfill] {key} 补下载完成（未消耗新 credit）")
                        results[key] = meta
                        continue
                    log(f"[warn] {key} 补下载后仍无效，重建任务（将重新计费）")
                else:
                    log(f"[warn] {key} 模型 URL 失效且本地无效，重建任务（将重新计费）")
                ledger.update(key, status="FAILED", task_error="本地 glb 无效且无法补下载")
                # 落入下方重建流程
            else:
                log(f"[skip] {key} 已 {done['status']}")
                results[key] = done
                continue

        # 之前失败/取消的任务清掉再重提（PENDING 任务 DELETE 会退回 credit）
        old = ledger.get(key)
        if old and old.get("task_id") and old.get("status") in ("FAILED", "CANCELED"):
            try:
                client.delete_task(old["task_id"])
            except requests.HTTPError:
                pass  # 清理失败不阻塞重提

        payload = build_task_payload(rec["prompt"], preset)
        try:
            task_id = client.create_task(payload)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            if code == 402:
                raise SystemExit("额度不足（402）：先充值或减少规模后重跑") from e
            raise
        ledger.update(key, pid=rec["pid"], preset=preset, prompt=rec["prompt"],
                      task_id=task_id, status="PENDING")
        log(f"[create] {key} → {task_id}")

        task = poll_task(client, task_id, poll_interval, poll_timeout, log)
        status = task.get("status")
        credits = task.get("consumed_credits")
        ledger.update(key, status=status, credits=credits,
                      task_error=task.get("task_error"), **echo_fields(preset, task))
        if status != "SUCCEEDED":
            log(f"[fail] {key} status={status} error={task.get('task_error')}")
            results[key] = {"status": status}
            continue

        meta = _store_outputs(client, key, rec, preset, task, raw_dir)
        log(f"[done] {key} glb={'yes' if meta['has_glb'] else 'NO'} credits={credits}")
        results[key] = meta

    return results


def verify_ledger_models(ledger: TaskLedger, raw_dir: Path | None = None) -> list[str]:
    """校验台账内全部 SUCCEEDED 模型的本地 GLB 完整性，返回坏键列表。"""
    raw_dir = raw_dir or (DATA_DIR / "raw")
    bad = []
    for key, rec in sorted(ledger.items.items()):
        if rec.get("status") != "SUCCEEDED":
            continue
        if not glb_valid(raw_dir / key / "model.glb"):
            bad.append(key)
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Meshy 批量生成（幂等）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--smoke", action="store_true", help="1 条 prompt × 2 档位")
    g.add_argument("--all", action="store_true", help="20 条全量（--preset 选档）")
    g.add_argument("--status", action="store_true", help="只看台账汇总")
    g.add_argument("--verify", action="store_true", help="校验全部本地模型 GLB 完整性")
    g.add_argument("--check-api", action="store_true",
                   help="校验 API key 可用（走任务列表端点，不消耗 credits）")
    ap.add_argument("--preset", choices=sorted(("standard", "smart-topology")),
                    default="standard")
    ap.add_argument("--no-backup", action="store_true",
                    help="批处理结束后不自动备份原始模型")
    args = ap.parse_args(argv)

    cfg = load_config()
    if args.status:
        s = TaskLedger().summary()
        print(json.dumps(s, ensure_ascii=False))
        return 0

    if args.check_api:
        # 为什么要这一步：生成是付费动作，key 配错/额度耗尽之前先探一次，
        # 避免跑到第一条任务才发现（`list_tasks(page_size=1)` 是官方认可的最小鉴权检查，
        # 见 `.claude/memory/2026-09-21-meshy-users-me-endpoint-404.md`）。
        try:
            tasks = MeshyClient(get_api_key(cfg)).list_tasks(page_size=1)
        except Exception as e:                      # noqa: BLE001 —— 任何失败都只报不抛
            print(f"check-api: 失败（{type(e).__name__}: {e}）")
            return 1
        print(f"check-api: OK（任务列表端点可达，返回 {len(tasks) if isinstance(tasks, list) else '?'} 条）")
        return 0

    if args.verify:
        ledger = TaskLedger()
        bad = verify_ledger_models(ledger)
        total = sum(1 for r in ledger.items.values() if r.get("status") == "SUCCEEDED")
        print(f"verify: {total - len(bad)}/{total} 有效" + (f"；损坏: {bad}" if bad else ""))
        return 0 if not bad else 1

    client = MeshyClient(get_api_key(cfg))
    prompts = load_prompts()

    if args.smoke:
        plan = plan_smoke(prompts)
    else:
        plan = plan_all(prompts, args.preset)

    ledger = TaskLedger()
    results = run_batch(plan, cfg, client, ledger)
    ok = sum(1 for r in results.values() if r.get("status") == "SUCCEEDED")
    print(f"batch finished: {ok}/{len(results)} SUCCEEDED; ledger={json.dumps(ledger.summary())}")
    if not args.no_backup:
        run_backup(cfg)
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        sys.exit(2)
