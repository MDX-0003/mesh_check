"""piece store：拆分产物化（PLAN-05 P1）。

把 weld(weld_digits) + split_components 的结果落盘为持久产物，供渲染单件通道、
交付图证等所有"需要单件网格文件"的消费者复用——拆分一次，全流程共享：

    data/raw/<key>/pieces/<rank>.glb   全部组件的单件网格（模型坐标系）
    data/raw/<key>/pieces/manifest.json
        {"version", "weld_digits", "n_pieces", "source": {"size", "mtime_ns"},
         "pieces": [{"rank", "n_faces", "glb"}]}

`glb` 一律是**相对模型目录**（`data/raw/<key>/`）的路径（如 `pieces/0.glb`）：manifest
会随交付/快照被复制出去，绝对路径等于把开发机的盘符和目录名一起发出去（2026-09-23
实测：交付快照里 20 份 manifest 都带着本机全路径）。需要绝对路径的地方用 `piece_glbs`
解析——trimesh 加载与传给 Blender 的 argv 都必须绝对。

拆分确定性取决于 (model.glb 内容, weld_digits)：manifest 记录源 GLB 的
size/mtime_ns 指纹，不匹配（模型重下/重算）或缺文件即重建；否则 ensure 直接
返回现有 manifest，不再重复拆分。

注意边界：`part_features` / `detect` 的 in-memory 拆分暂不改造为消费 store
（T2 拆分 <1s，改读大量小文件反而更慢；其重算去重属输入指纹方向）。本 store 的
消费者是"需要单件网格文件"的渲染与交付层。

改本模块必改 tests/test_piece_store.py。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MANIFEST_NAME = "manifest.json"


def store_dir(key: str, data_dir: Path) -> Path:
    return data_dir / "raw" / key / "pieces"


def _fingerprint(glb: Path) -> dict[str, Any]:
    st = glb.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _glb_rel(rank: int | str) -> str:
    """单件 GLB 在 manifest 里的路径表示（相对模型目录，见模块 docstring）。"""
    return f"pieces/{int(rank)}.glb"


def piece_glbs(manifest: dict[str, Any], key: str, data_dir: Path) -> dict[str, Path]:
    """manifest 的相对路径 → 绝对 Path（trimesh 加载 / 传给 Blender 的 argv 用）。"""
    base = data_dir / "raw" / key
    return {rank: base / rel for rank, rel in (manifest.get("glbs") or {}).items()}


def _normalize_paths(manifest: dict[str, Any], path: Path) -> dict[str, Any]:
    """把 manifest 里的单件路径统一回相对形式；有变化才落盘（幂等）。

    老产物写的是绝对路径，`_valid` 不校验路径形式，所以它们能一直命中缓存。
    直接重写字段而不是整表重建：拆分结果本身没变，重拆要跑几分钟。
    """
    changed = False
    for entry in manifest.get("pieces", []):
        rel = _glb_rel(entry["rank"])
        if entry.get("glb") != rel:
            entry["glb"] = rel
            changed = True
    rel_glbs = {str(entry["rank"]): entry["glb"]
                for entry in manifest.get("pieces", [])}
    if manifest.get("glbs") != rel_glbs:
        manifest["glbs"] = rel_glbs
        changed = True
    if changed:
        _write(path, manifest)
    return manifest


def _write(path: Path, manifest: dict[str, Any]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                   encoding="utf-8", newline="\n")
    tmp.replace(path)


def _valid(manifest: dict[str, Any], glb: Path, weld_digits: int,
           pieces_dir: Path) -> bool:
    """manifest 与源指纹、文件完整性全部匹配才算有效。"""
    if manifest.get("version") != 1:
        return False
    if manifest.get("weld_digits") != weld_digits:
        return False
    if manifest.get("source") != _fingerprint(glb):
        return False
    for p in manifest.get("pieces", []):
        if not (pieces_dir / f"{p['rank']}.glb").is_file():
            return False
    return True


def ensure_store(key: str, data_dir: Path, weld_digits: int = 5,
                 force: bool = False) -> dict[str, Any]:
    """确保 key 的拆分产物存在且与源模型指纹一致，返回 manifest。

    manifest.pieces 按 rank 升序，glb 为相对模型目录的路径（见模块 docstring）；
    glbs 为 {str(rank): 相对路径} 的便捷映射。force=True 强制重建。
    """
    import trimesh

    from meshq.core.mesh_ops import split_components, weld

    glb = data_dir / "raw" / key / "model.glb"
    if not glb.is_file():
        raise FileNotFoundError(f"{glb} 不存在")
    pieces_dir = store_dir(key, data_dir)
    manifest_path = pieces_dir / MANIFEST_NAME

    if not force and manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if _valid(manifest, glb, weld_digits, pieces_dir):
                return _normalize_paths(manifest, manifest_path)
        except json.JSONDecodeError:
            pass

    mesh = trimesh.load(glb, force="mesh")
    comps = split_components(weld(mesh, weld_digits))
    pieces_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    for rank, comp in enumerate(comps):
        dest = pieces_dir / f"{rank}.glb"
        comp.export(dest)
        entries.append({"rank": rank, "n_faces": int(len(comp.faces)),
                        "glb": _glb_rel(rank)})

    manifest = {
        "version": 1,
        "weld_digits": int(weld_digits),
        "n_pieces": len(entries),
        "source": _fingerprint(glb),
        "pieces": entries,
        "glbs": {str(e["rank"]): e["glb"] for e in entries},
    }
    _write(manifest_path, manifest)
    return manifest
