"""共享基础：配置加载、prompt 台账读取、Meshy API client、任务台账（幂等的根基）。

约定（见 CLAUDE.md）：
- 设备相关配置只从 config.toml 读，脚本内零硬编码；
- 任务台账 data/tasks.json 是生成幂等的唯一事实来源。
"""

from __future__ import annotations

import json
import os
import struct
import tomllib
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[2]   # 仓库根（模块现位于 meshq/<层>/<模块>.py）
DATA_DIR = ROOT / "data"
TASKS_FILE = DATA_DIR / "tasks.json"
PROMPTS_FILE = ROOT / "prompts.jsonl"

BASE_URL = "https://api.meshy.ai/openapi/v2"


class ConfigError(RuntimeError):
    """配置缺失/非法。消息面向操作者，给出下一步动作。"""


# ---------------------------------------------------------------- config

def load_config(path: Path | None = None) -> dict[str, Any]:
    p = path or (ROOT / "config.toml")
    if not p.exists():
        raise ConfigError(f"缺少 {p.name}：`cp config.example.toml config.toml` 后填写")
    with open(p, "rb") as f:
        cfg = tomllib.load(f)
    for section in ("blender", "meshy", "detect", "render", "poll", "parts"):
        if section not in cfg:
            raise ConfigError(f"config 缺少 [{section}] 段（对照 config.example.toml）")
    return cfg


def get_api_key(config: dict[str, Any], env: dict[str, str] | None = None) -> str:
    """config 优先，环境变量回退。"""
    env = os.environ if env is None else env
    key = (config.get("meshy", {}).get("api_key") or "").strip()
    key = key or (env.get("MESHY_API_KEY") or "").strip()
    if not key:
        raise ConfigError("API key 未配置：填 config.toml [meshy] api_key，或设环境变量 MESHY_API_KEY")
    return key


def get_blender_exe(config: dict[str, Any]) -> Path:
    """取 Blender 可执行文件；空值与错路径分别给出可操作的提示。

    空值单独分支：`Path("")` 会退化成 `.`，报错会写成"blender 可执行文件不存在：."
    （实测踩过），对"还没填"这种最常见情形不友好。
    """
    raw = str(config["blender"].get("path") or "").strip()
    if not raw:
        raise ConfigError("config.toml 的 [blender] path 未填写：填 Blender 可执行文件的"
                          "全路径（只有渲染/重渲阶段需要它）")
    exe = Path(raw)
    if not exe.is_file():
        raise ConfigError(f"blender 可执行文件不存在：{exe}（改 config.toml [blender] path）")
    return exe


def glb_valid(path: Path) -> bool:
    """GLB 完整性：≥12 字节、magic='glTF'、头部长度字段 == 实际大小。

    下载中断会留下截断文件——只看"文件存在"会漏检（见 memory 2026-09-21）。
    """
    try:
        if path.stat().st_size < 12:
            return False
        with open(path, "rb") as f:
            head = f.read(12)
        if head[:4] != b"glTF":
            return False
        declared_length = struct.unpack("<I", head[8:12])[0]
        return declared_length == path.stat().st_size
    except OSError:
        return False


# ---------------------------------------------------------------- 批次范围

# 模型键形如 `p01@smart-topology`；批次 → 键后缀。
BATCH_SUFFIX = {"smart-topology": "@smart-topology", "standard": "@standard"}

# 按批次分文件的汇总类产物：只有对应批次才该带上（其余同族文件是别的批次的）。
BATCH_SUMMARY_FILES = {"summary_t2.json": "smart-topology",
                       "summary-standard-v2.json": "standard",
                       "summary-standard.json": "standard",     # 旧命名，同样只属 standard
                       "summary.json": "standard"}              # 冻结的 standard 基线


def in_batch(key: object, batch: str | None) -> bool:
    """该模型键是否属于批次；`batch=None` 表示不过滤（整库口径）。

    无 `@` 后缀的键（如探针 `probe-poly15k`）**不属于任何交付批次**——按批次取用时排除。
    交付附录与数据包两处都按这条规则裁剪，避免"交付讲 T2、附录里却混着 standard 记录"。
    """
    if not batch:
        return True
    return str(key).endswith(BATCH_SUFFIX[batch])


# ---------------------------------------------------------------- JSONL 读取

def iter_jsonl(path: Path):
    """逐行读 JSONL，**报错带 `文件名:行号`**。

    为什么收口到这里：此前 14 处各写一遍 `read_text().splitlines()` + `json.loads`，
    其中 11 处出错只抛裸 `json.JSONDecodeError`——产物动辄上千行，看不出坏在哪一行
    （`data/findings.jsonl`、`metrics.jsonl`、`parts.jsonl` 都是这种规模）。
    """
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path.name}:{line_no} JSON 非法：{e}") from e


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL → 记录列表；文件不存在返回空表（"没有就跳过"是调用方的常态）。"""
    if not path.is_file():
        return []
    return list(iter_jsonl(path))


def read_jsonl_by_key(path: Path, field: str = "key") -> dict[str, dict[str, Any]]:
    """JSONL → `{<field>: 记录}`；同名键后者覆盖前者（与各产物的"每键一行"契约一致）。

    此前这个形状在 5 处各写一遍（metrics / part_features / part_verdict.load_jsonl /
    detect.load_jsonl / report.load_verdicts），名字还各不相同。
    """
    items: dict[str, dict[str, Any]] = {}
    for rec in read_jsonl(path):
        if field in rec:
            items[rec[field]] = rec
    return items


# ---------------------------------------------------------------- prompts

def load_prompts(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """prompts.jsonl → {pid: record}。"""
    p = path or PROMPTS_FILE
    items: dict[str, dict[str, Any]] = {}
    for line_no, rec in enumerate(iter_jsonl(p), 1):
        pid = rec.get("pid")
        if not pid or pid in items:
            raise ValueError(f"{p.name}:{line_no} pid 缺失或重复")
        if len(rec.get("prompt", "")) > 800:
            raise ValueError(f"{p.name}:{line_no} prompt 超过 800 字符上限")
        items[pid] = rec
    return items


# ---------------------------------------------------------------- 生成档位（PLAN-00 D7）

MODEL_PRESETS: dict[str, dict[str, Any]] = {
    # standard/meshy-7：should_remesh=false 为官方推荐的最高质量设置
    "standard": {"model_type": "standard", "ai_model": "meshy-7", "should_remesh": False},
    # smart-topology/meshy-t2：默认 4000 面、三角形、官方称"原生分离部件"；
    # 显式拉满面数上限（PLAN-03 D3：计费按调用计、与面数无关，无理由不用满）
    "smart-topology": {"model_type": "smart-topology", "ai_model": "meshy-t2",
                       "target_polycount": 15000},
}


def build_task_payload(
    prompt: str,
    preset: str,
    target_formats: list[str] | None = None,
) -> dict[str, Any]:
    if preset not in MODEL_PRESETS:
        raise ConfigError(f"未知生成档位：{preset}（可选：{sorted(MODEL_PRESETS)}）")
    if not prompt or len(prompt) > 800:
        raise ValueError("prompt 为空或超过 800 字符")
    payload: dict[str, Any] = {
        "mode": "preview",  # 仅几何，无贴图；refine 对质检零增益且贵数倍（PLAN-00 D6）
        "prompt": prompt,
        **MODEL_PRESETS[preset],
        "target_formats": target_formats or ["glb"],
    }
    return payload


# ---------------------------------------------------------------- API client

class MeshyClient:
    """Meshy OpenAPI v2 薄封装。网络健壮性（重试/退避）由调用方按需处理。"""

    TERMINAL_OK = "SUCCEEDED"
    TERMINAL_BAD = ("FAILED", "CANCELED")

    def __init__(self, api_key: str, base_url: str = BASE_URL, timeout: int = 30,
                 session: requests.Session | None = None):
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._http = session or requests.Session()

    def list_tasks(self, page_size: int = 1) -> list[dict[str, Any]]:
        r = self._http.get(f"{self._base}/text-to-3d",
                           params={"page_size": page_size},
                           headers=self._headers, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def create_task(self, payload: dict[str, Any]) -> str:
        r = self._http.post(f"{self._base}/text-to-3d", json=payload,
                            headers=self._headers, timeout=self._timeout)
        r.raise_for_status()
        d = r.json()
        task_id = d.get("id") or d.get("result")
        if not task_id:
            raise ValueError(f"create_task 响应缺少任务 id：{list(d)}")
        return task_id

    def get_task(self, task_id: str) -> dict[str, Any]:
        r = self._http.get(f"{self._base}/text-to-3d/{task_id}",
                           headers=self._headers, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def delete_task(self, task_id: str) -> None:
        r = self._http.delete(f"{self._base}/text-to-3d/{task_id}",
                              headers=self._headers, timeout=self._timeout)
        if r.status_code not in (200, 204):
            r.raise_for_status()

    def download(self, url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._http.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    f.write(chunk)


# ---------------------------------------------------------------- 任务台账

class TaskLedger:
    """tasks.json：{pid@preset → 记录}。生成幂等的唯一事实来源。"""

    TERMINAL_OK = "SUCCEEDED"
    TERMINAL_BAD = ("FAILED", "CANCELED")

    def __init__(self, path: Path | None = None):
        self.path = path or TASKS_FILE
        self.items: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            for rec in json.loads(self.path.read_text(encoding="utf-8")):
                self.items[rec["key"]] = rec

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(list(self.items.values()), ensure_ascii=False, indent=1),
            encoding="utf-8",
            newline="\n",
        )

    def get(self, key: str) -> dict[str, Any] | None:
        return self.items.get(key)

    def update(self, key: str, **fields: Any) -> dict[str, Any]:
        rec = self.items.setdefault(key, {"key": key})
        rec.update(fields)
        self.save()
        return rec

    def needs_task(self, key: str) -> bool:
        """True=需要创建任务；False=SUCCEEDED 跳过或 PENDING/IN_PROGRESS 续查。"""
        rec = self.items.get(key)
        if rec is None:
            return True
        if rec.get("status") == self.TERMINAL_OK:
            return False
        if rec.get("status") in self.TERMINAL_BAD or rec.get("task_id") is None:
            return True
        return False

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for rec in self.items.values():
            out[rec.get("status", "?")] = out.get(rec.get("status", "?"), 0) + 1
        return out
