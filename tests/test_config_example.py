"""config.example.toml 是配置结构的唯一模板：结构变更必须先改模板再改加载器，
保证新克隆的仓库填完 config 就能跑。此测试守护模板本身的结构完整。"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_config_example_parses():
    data = tomllib.loads((ROOT / "config.example.toml").read_text(encoding="utf-8"))

    # `[blender] path` 允许为空：模板是给接收方照抄的，不该带作者的机器路径；
    # 只有渲染阶段需要它，缺了会由 get_blender_exe 明确报错（不是静默跳过）。
    assert isinstance(data["blender"]["path"], str), "[blender] path 应为字符串"
    assert isinstance(data["meshy"]["api_key"], str), "[meshy] api_key 应为字符串"

    poll = data["poll"]
    assert poll["interval_seconds"] > 0
    assert poll["timeout_seconds"] >= poll["interval_seconds"]

    detect = data["detect"]
    assert 0 < detect["diag_ratio"] < 1
    assert 0 < detect["vol_ratio"] < 1
    assert 0 < detect["frag_reject_ratio"] < 1

    render = data["render"]
    assert len(render["resolution"]) == 2
    assert all(v > 0 for v in render["resolution"])

    lookdev = data["render"]["lookdev"]
    assert lookdev["background_mode"] in ("gradient", "solid")
    for key in ("bg_gradient_top", "bg_gradient_bottom", "bg_solid_color",
                "key_color", "rim_color", "wire_color"):
        value = lookdev[key]
        assert value.startswith("#") and len(value) == 7, f"{key} 应为 #RRGGBB"
    assert all(lookdev[k] > 0 for k in
               ("key_energy", "rim_energy", "fill_energy", "emission_strength"))
    assert -90 < lookdev["key_azimuth"] < 90
    assert 0 < lookdev["key_elevation"] < 90
    assert lookdev["view_transform"] in ("AgX", "Standard", "Filmic", "Filmic Log")
    assert lookdev["highlight_view_transform"] in ("AgX", "Standard", "Filmic", "Filmic Log")
    assert lookdev["wire_display_faces"] > 0
    assert 0 < lookdev["wire_thickness"] < 0.05
    assert lookdev["wire_ssaa"] in (1, 2)
    assert isinstance(lookdev["ground_plane"], bool)


def test_example_template_has_no_machine_specific_paths():
    """模板是给接收方照抄的：**值里不得出现本机盘符/家目录路径**。

    2026-09-23 实测漏网：`[blender] path` 里写着作者的便携版 Blender 全路径
    （`D:\\GitProject\\…\\blender.exe`）——既泄漏本机目录布局，又让照抄的人拿到错路径。
    当时的扫描只查了 `D:\\Programs` 与 `C:\\Users` 两个前缀，故没抓到这条。

    只查**配置值**、不查注释：注释里按平台列出的示例路径（`C:/Program Files/…`、
    `/opt/…`）是有意写的教学内容。`.claude/memory/` 里的环境记录属过程材料，
    按既有口径不在本守卫范围内。
    """
    import re
    data = tomllib.loads((ROOT / "config.example.toml").read_text(encoding="utf-8"))
    values: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            values.append(node)

    walk(data)
    offenders = [v for v in values if re.search(r"[A-Za-z]:[\\/]", v)]
    assert not offenders, f"config.example.toml 的值里含本机路径：{offenders}"


def test_example_template_documents_blender_path():
    """`[blender] path` 必须留空占位，并在注释里写明"填什么 + 实测版本 + 各平台示例"。"""
    raw = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    data = tomllib.loads(raw)

    assert data["blender"]["path"] == ""            # 占位为空，勿带作者机器的路径
    assert "全路径" in raw and "5.0.1" in raw       # 说明"填什么"与实测版本
    assert "只有" in raw and "渲染" in raw           # 说明"哪些阶段需要它"
