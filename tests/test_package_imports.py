"""包边界守卫：import 目标必须是标准库 / 已知第三方 / `meshq.*`。

**为什么需要这道守卫**（2026-09-23 实测）：包收敛那次迁移里，Blender 侧
`meshq/blender/render_one.py` 残留了旧前缀 `from blender.bpy_geom import …`。
`render_one.py` 由 `blender.exe --background --python <绝对路径>` 执行，**不在任何单测的
导入路径上**，于是 `pytest` 全绿、只有真跑一次渲染才报
`ModuleNotFoundError: No module named 'blender'`。用 AST 静态扫 import 目标，
这类"裸名 / 旧前缀"引用在 CI 层就能拦下来，不必等到渲染。

判据故意做得**宽**（只拦"不属于任何已知命名空间"的目标）：它要抓的是包边界错误，
不是风格问题——所以标准库用 `sys.stdlib_module_names` 判定，第三方只列本项目实际依赖的。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# 本项目实际用到的第三方顶层模块（新增依赖时在此登记）
THIRD_PARTY = {"bpy", "bmesh", "mathutils", "bpy_extras", "PIL", "jinja2",
               "numpy", "trimesh", "scipy", "requests", "pytest"}
# 仓库内允许的顶层命名空间（含 tests/ 下的兄弟测试模块：例如 review_serve 的用例复用 deliver 的假件）
INTERNAL = {"meshq", "tests", "fakes", "conftest"} | {
    p.stem for p in (Path(__file__).resolve().parent).glob("*.py")}

# 需要"被直接执行"（`blender --python <路径>` / `python <路径>`）的脚本：
# 它们必须自己把仓库根插进 sys.path，否则绝对导入 `meshq.*` 会失败。
DIRECT_EXEC_ENTRIES = ("meshq/blender/render_one.py",)


def import_targets(source: str) -> list[tuple[int, str]]:
    """AST 取所有 import 的顶层目标 → [(行号, 顶层模块名)]。

    用 AST 而不是正则：多行括号 import、行内注释、缩进都不会误判。
    """
    tree = ast.parse(source)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name.split(".")[0]))
        elif isinstance(node, ast.ImportFrom):
            if node.level:                      # 相对导入：留给包内自洽性，不在本守卫范围
                continue
            if node.module:
                found.append((node.lineno, node.module.split(".")[0]))
    return found


def test_guard_helper_rejects_bare_and_stale_names():
    """守卫自身不能是空转的：合成一段旧写法源码，必须被判为越界。"""
    sample = "from blender.bpy_geom import x\nimport geometry\nfrom meshq.core import common\n"
    targets = {mod for _, mod in import_targets(sample)}

    assert targets == {"blender", "geometry", "meshq"}
    assert {"blender", "geometry"} - (set(sys.stdlib_module_names) | THIRD_PARTY | INTERNAL)


@pytest.mark.parametrize("py", sorted(Path("meshq").rglob("*.py")), ids=lambda p: p.as_posix())
def test_package_imports_stay_in_known_namespaces(py: Path):
    """包内每个 import 目标都必须可解释：标准库 / 已知第三方 / meshq.*。"""
    allowed = set(sys.stdlib_module_names) | THIRD_PARTY | INTERNAL
    offenders = [(line, mod) for line, mod in import_targets(py.read_text(encoding="utf-8"))
                 if mod not in allowed]

    assert not offenders, (
        f"{py}: import 目标越界（裸名或旧前缀？）{offenders}——"
        "包内引用一律写 `from meshq.<层>.<模块> import …`")


@pytest.mark.parametrize("py", sorted(Path("tests").glob("*.py")), ids=lambda p: p.name)
def test_test_imports_stay_in_known_namespaces(py: Path):
    """测试同样只能引用标准库 / 第三方 / meshq.*（防止残留旧模块名）。"""
    allowed = set(sys.stdlib_module_names) | THIRD_PARTY | INTERNAL
    offenders = [(line, mod) for line, mod in import_targets(py.read_text(encoding="utf-8"))
                 if mod not in allowed]

    assert not offenders, f"{py}: import 目标越界 {offenders}"


def _line_of_sys_path_insert(tree: ast.AST) -> int | None:
    """`sys.path.insert(...)` 调用的行号（没有则 None）。"""
    lines = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "insert"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "path"):
            lines.append(node.lineno)
    return min(lines) if lines else None


@pytest.mark.parametrize("rel", DIRECT_EXEC_ENTRIES)
def test_direct_exec_entries_bootstrap_package_root(rel: str):
    """被直接执行的脚本必须把仓库根插进 sys.path，否则 `from meshq.…` 在 Blender 内必失败。

    这是"只有真渲染才发现"那类事故的静态防线：断言 `sys.path.insert` 出现在**第一条 meshq
    导入之前**。用 AST 行号而不是文本搜索——文件头的文档字符串里也会出现 `from meshq…`
    （第一版守卫就因此误判过一次）。
    """
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    meshq_imports = [line for line, mod in import_targets((ROOT / rel).read_text(encoding="utf-8"))
                     if mod == "meshq"]
    insert_at = _line_of_sys_path_insert(tree)

    assert insert_at is not None, f"{rel}: 缺少 sys.path 引导（被 --python 直接执行时必须自己补仓库根）"
    assert meshq_imports, f"{rel}: 未找到 meshq 导入（该文件应当按包绝对导入）"
    assert insert_at < min(meshq_imports), (
        f"{rel}: sys.path 引导必须在第一条 meshq 导入之前"
        f"（引导在第 {insert_at} 行，首个 meshq 导入在第 {min(meshq_imports)} 行）")
