import struct
from pathlib import Path

import pytest

from meshq.core.common import glb_valid


def real_glb(path: Path, length=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b"glTF" + struct.pack("<I", 2)
    total = 12 + 8 if length is None else length
    path.write_bytes(payload + struct.pack("<I", total) + b"\x00" * (total - 12))


def test_valid_glb(tmp_path):
    p = tmp_path / "m.glb"
    real_glb(p)
    assert glb_valid(p) is True


def test_truncated_glb_rejected(tmp_path):
    p = tmp_path / "m.glb"
    real_glb(p, length=1000)      # 先写满 1000 字节
    with open(p, "r+b") as f:     # 再截断到 20 字节：头部声明与实际不符
        f.truncate(20)
    assert glb_valid(p) is False


def test_short_or_fake_glb_rejected(tmp_path):
    p = tmp_path / "short.glb"
    p.write_bytes(b"glTF")
    assert glb_valid(p) is False
    q = tmp_path / "fake.glb"
    q.write_bytes(b"fake-glb-bytes----")
    assert glb_valid(q) is False


def test_missing_glb_rejected(tmp_path):
    assert glb_valid(tmp_path / "nope.glb") is False
