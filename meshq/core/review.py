"""review.jsonl 契约：模型级人工裁决记录（PLAN-05 §二）。

review.jsonl 是审核台的状态文件、生成器的落位依据、交付时的复核档案——三重身份，
因此读写与校验在此唯一收口：

- 每模型一条**最新**记录：{key, disposition, reason, reviewed_by, reviewed_at}；
- disposition ∈ pass | fail | pending（中文显示名由 DISPOSITION_LABEL 映射）；
- 写入用临时文件原子替换，审核台与生成器可并发读、不会读到半截文件；
- 生成器重跑以本文件为准恢复落位（幂等），人工裁决不被覆盖。

落位规则（PLAN-05 §一，"检出"限定为待复核检出 tier=review）：
  无待复核检出 → pass；≥1 → pending；fail 仅由人工裁决产生。
plan_dispositions() 是该规则的唯一实现，生成器与 pipeline 干跑共用。

改本模块必改 tests/test_review.py。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from meshq.core.common import iter_jsonl

DISPOSITION_PASS = "pass"
DISPOSITION_FAIL = "fail"
DISPOSITION_PENDING = "pending"
DISPOSITIONS = (DISPOSITION_PASS, DISPOSITION_FAIL, DISPOSITION_PENDING)

# 页面显示名（内部目录/记录一律 ASCII，中文只做展示）
DISPOSITION_LABEL = {DISPOSITION_PASS: "确定通过",
                     DISPOSITION_FAIL: "确定不通过",
                     DISPOSITION_PENDING: "待人工裁决"}

# ASCII 目录名 ↔ 显示名（results 三类别）
DISPOSITION_DIR = {DISPOSITION_PASS: "passed",
                   DISPOSITION_FAIL: "failed",
                   DISPOSITION_PENDING: "pending"}

_REQUIRED = ("key", "disposition")


def make_record(key: str, disposition: str, reason: str = "",
                reviewed_by: str = "reviewer",
                reviewed_at: str | None = None) -> dict:
    """构造一条裁决记录，取值在此唯一校验。"""
    if not key:
        raise ValueError("key 缺失")
    if disposition not in DISPOSITIONS:
        raise ValueError(f"disposition 非法：{disposition}（可选：{DISPOSITIONS}）")
    from datetime import datetime, timezone

    return {"key": str(key), "disposition": disposition, "reason": str(reason),
            "reviewed_by": str(reviewed_by),
            "reviewed_at": reviewed_at
            or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def load_reviews(path: Path) -> dict[str, dict]:
    """review.jsonl → {key: 记录}（每 key 取最后一条）；文件缺失返回空表。"""
    if not path.exists():
        return {}
    items: dict[str, dict] = {}
    for line_no, rec in enumerate(iter_jsonl(path), 1):
        try:
            validated = make_record(**{k: rec.get(k) for k in _REQUIRED},
                                    reason=rec.get("reason", ""),
                                    reviewed_by=rec.get("reviewed_by", "reviewer"),
                                    reviewed_at=rec.get("reviewed_at"))
        except (TypeError, ValueError) as e:
            raise ValueError(f"{path.name}:{line_no} 裁决记录非法：{e}") from e
        items[validated["key"]] = validated
    return items


def write_reviews(path: Path, records: dict[str, dict]) -> None:
    """按 key 排序原子写出（临时文件 + os.replace，并发读不会见半截文件）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(records[k], ensure_ascii=False) for k in sorted(records)]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def upsert_review(path: Path, key: str, disposition: str, reason: str = "",
                  reviewed_by: str = "reviewer") -> dict:
    """单条裁决 → 合并进 review.jsonl（保留其他模型的记录），返回该模型最新记录。"""
    records = load_reviews(path)
    rec = make_record(key, disposition, reason=reason, reviewed_by=reviewed_by)
    records[key] = rec
    write_reviews(path, records)
    return rec


def plan_dispositions(findings_rows: list[dict], reviews: dict[str, dict],
                      keys: list[str]) -> dict[str, dict]:
    """落位规则（PLAN-05 §一）的唯一实现。

    - 人工裁决（reviews）优先，永不覆盖；
    - 否则按待复核检出（tier=review）计数：0 → pass，≥1 → pending；
    - fail 只可能来自人工裁决。
    返回 {key: {"disposition", "source": "human"|"rule", "review_count", "reason"}}。
    """
    review_counts: dict[str, int] = {}
    for r in findings_rows:
        if r.get("tier") == "review":
            review_counts[r["key"]] = review_counts.get(r["key"], 0) + 1
    out = {}
    for key in keys:
        human = reviews.get(key)
        if human:
            out[key] = {"disposition": human["disposition"], "source": "human",
                        "review_count": review_counts.get(key, 0),
                        "reason": human.get("reason", "")}
        elif review_counts.get(key, 0) > 0:
            out[key] = {"disposition": DISPOSITION_PENDING, "source": "rule",
                        "review_count": review_counts[key],
                        "reason": f"待复核检出 {review_counts[key]} 条"}
        else:
            out[key] = {"disposition": DISPOSITION_PASS, "source": "rule",
                        "review_count": 0, "reason": "无待复核检出"}
    return out
