"""piece_store：拆分产物化的守卫（PLAN-05 P1）。

覆盖：store 创建（全组件单件 GLB + manifest 指纹）、幂等命中（不重复拆分）、
指纹失效重建（模型变化 / weld_digits 变化 / 文件缺失）、异常输入。
"""

import json
from pathlib import Path

import trimesh
import pytest

from fakes import write_valid_glb
from meshq.core.piece_store import ensure_store, piece_glbs, store_dir


def real_glb(path: Path, n=2):
    """真网格 GLB：n 个互不接触的盒体（连通域数 = n）。"""
    meshes = []
    for i in range(n):
        m = trimesh.creation.box(extents=(1, 1, 1))
        m.apply_translation((i * 5.0, 0, 0))
        meshes.append(m)
    path.parent.mkdir(parents=True, exist_ok=True)
    trimesh.util.concatenate(meshes).export(path)


@pytest.fixture()
def world(tmp_path):
    data = tmp_path / "data"
    real_glb(data / "raw" / "pX@smart-topology" / "model.glb", n=2)
    return data


def test_ensure_creates_glbs_and_manifest(world):
    m = ensure_store("pX@smart-topology", world, weld_digits=5)
    pieces = world / "raw" / "pX@smart-topology" / "pieces"
    assert m["n_pieces"] == 2
    assert sorted(m["glbs"]) == ["0", "1"]
    assert (pieces / "0.glb").is_file() and (pieces / "1.glb").is_file()
    assert (pieces / "manifest.json").is_file()
    assert m["source"]["size"] > 0


def test_ensure_hit_does_not_rebuild(world):
    key = "pX@smart-topology"
    m1 = ensure_store(key, world, weld_digits=5)
    before = (store_dir(key, world) / "0.glb").stat().st_mtime_ns
    m2 = ensure_store(key, world, weld_digits=5)
    assert m1 == m2
    assert (store_dir(key, world) / "0.glb").stat().st_mtime_ns == before  # 未重建


def test_fingerprint_invalidates_on_model_change(world):
    key = "pX@smart-topology"
    m1 = ensure_store(key, world, weld_digits=5)
    assert m1["n_pieces"] == 2
    real_glb(world / "raw" / key / "model.glb", n=3)          # 模型内容变化
    m2 = ensure_store(key, world, weld_digits=5)
    assert m2["n_pieces"] == 3                                 # 指纹失配 → 重建


def test_weld_digits_change_invalidates(world):
    key = "pX@smart-topology"
    ensure_store(key, world, weld_digits=5)
    m = ensure_store(key, world, weld_digits=4)                # 参数变化 → 重建
    assert m["weld_digits"] == 4


def test_corrupt_manifest_rebuilds(world):
    key = "pX@smart-topology"
    ensure_store(key, world, weld_digits=5)
    (store_dir(key, world) / "manifest.json").write_text("{broken", encoding="utf-8")
    m = ensure_store(key, world, weld_digits=5)
    assert m["n_pieces"] == 2


def test_missing_glb_in_store_rebuilds(world):
    key = "pX@smart-topology"
    ensure_store(key, world, weld_digits=5)
    (store_dir(key, world) / "1.glb").unlink()
    m = ensure_store(key, world, weld_digits=5)
    assert (store_dir(key, world) / "1.glb").is_file()         # 缺文件自动重建
    assert m["n_pieces"] == 2


def test_missing_model_raises(world):
    with pytest.raises(FileNotFoundError):
        ensure_store("ghost@smart-topology", world, weld_digits=5)


def test_force_rebuild(world):
    key = "pX@smart-topology"
    ensure_store(key, world, weld_digits=5)
    m = ensure_store(key, world, weld_digits=5, force=True)
    assert m["n_pieces"] == 2


def test_manifest_paths_are_relative(world):
    """manifest 会随交付快照复制出去，绝不能带本机绝对路径（2026-09-23 实测泄漏）。"""
    key = "pX@smart-topology"
    m = ensure_store(key, world, weld_digits=5)
    assert m["glbs"] == {"0": "pieces/0.glb", "1": "pieces/1.glb"}
    assert all(p["glb"] == f"pieces/{p['rank']}.glb" for p in m["pieces"])


def test_piece_glbs_resolves_against_model_dir(world):
    key = "pX@smart-topology"
    m = ensure_store(key, world, weld_digits=5)
    got = piece_glbs(m, key, world)
    assert got["0"] == world / "raw" / key / "pieces" / "0.glb"
    assert got["0"].is_file()


def test_legacy_absolute_paths_are_migrated_in_place(world):
    """老产物里的绝对路径在命中缓存时被就地归一（不重拆分）。"""
    key = "pX@smart-topology"
    ensure_store(key, world, weld_digits=5)
    mpath = store_dir(key, world) / "manifest.json"
    legacy = json.loads(mpath.read_text(encoding="utf-8"))
    abs_glb = str((world / "raw" / key / "pieces" / "0.glb").resolve())
    legacy["pieces"][0]["glb"] = abs_glb
    legacy["glbs"]["0"] = abs_glb
    mpath.write_text(json.dumps(legacy), encoding="utf-8")
    before = (store_dir(key, world) / "0.glb").stat().st_mtime_ns

    m = ensure_store(key, world, weld_digits=5)

    assert m["glbs"]["0"] == "pieces/0.glb"
    assert json.loads(mpath.read_text(encoding="utf-8"))["glbs"]["0"] == "pieces/0.glb"
    assert (store_dir(key, world) / "0.glb").stat().st_mtime_ns == before  # 未重拆分


def test_manifest_without_glbs_map_is_repaired(world):
    """缺 glbs 映射的老 manifest 也要补回（页面/渲染只读 glbs）。"""
    key = "pX@smart-topology"
    ensure_store(key, world, weld_digits=5)
    mpath = store_dir(key, world) / "manifest.json"
    legacy = json.loads(mpath.read_text(encoding="utf-8"))
    del legacy["glbs"]
    mpath.write_text(json.dumps(legacy), encoding="utf-8")

    m = ensure_store(key, world, weld_digits=5)

    assert sorted(m["glbs"]) == ["0", "1"]
