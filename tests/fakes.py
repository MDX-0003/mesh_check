"""tests 侧的共享假件：不依赖网络的 MeshyClient 依赖。"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any


def write_valid_glb(path: Path) -> None:
    """写入一个头部合法的最小 GLB（12 字节头，声明长度==实际长度）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"glTF" + struct.pack("<II", 2, 12))


class FakeResponse:
    def __init__(self, status_code: int = 200, json_body: Any = None):
        self.status_code = status_code
        self._body = json_body
        self.text = ""

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no json body")
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    """按 (method, path 结尾) 回放预设响应。"""

    def __init__(self, responses: dict[str, FakeResponse] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, str, Any]] = []

    def _match(self, url: str) -> FakeResponse:
        for suffix, resp in self.responses.items():
            if url.endswith(suffix):
                return resp
        raise AssertionError(f"未预设的请求：{url}")

    def get(self, url: str, **kw: Any) -> FakeResponse:
        self.calls.append(("GET", url, kw))
        return self._match(url)

    def post(self, url: str, **kw: Any) -> FakeResponse:
        self.calls.append(("POST", url, kw))
        return self._match(url)

    def delete(self, url: str, **kw: Any) -> FakeResponse:
        self.calls.append(("DELETE", url, kw))
        return self._match(url)
