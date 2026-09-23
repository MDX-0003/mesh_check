# 观变换与自发光强度的两个反向陷阱：AgX 去饱和 / Standard 过曝

- **日期**：2026-09-21
- **现象**：p13 高亮图（emission strength 2.0、纯红 Emission Color (1.0, 0.03, 0.03)）里
  悬浮碎片呈淡橙粉色，报告 260px 缩略图下几乎不可见——"缺陷被检出"在呈现层失效。
- **根因**：Blender 默认观变换 AgX 会把高亮度红色去饱和（"发粉"）。此前只调低
  emission strength 治标不治本；只要还在 AgX 下，红色自发光必然被压。
- **修复**：分通道色彩管理（PLAN-02 D3）——`base_*`/`wire_*` 通道保持 AgX 拿质感，
  `highlight_*` 通道渲染前切 `scene.view_settings.view_transform = "Standard"`
  （属性在 `scene.view_settings`，不在 `scene.render` 上）。Standard 下 emission 2.0
  为饱和警示红（p13/p18 目检确认）。
- **预防**：需要"标注色保持饱和"的渲染（缺陷标记、预警高亮），单独用 Standard 观变换
  通道；同一场景内切换观变换零成本，不要为迁就标注色牺牲整体质感。

---

## 补记（2026-09-23）：反向陷阱——Standard 下自发光会**过曝成白**

同一个旋钮（观变换 × emission strength）从另一边咬人：定位图设 `emission_strength = 1.0`
配 Standard 观变换，纯色自发光直接渲成**纯白**，三色强调全糊成一片、看不出任何区分。

- **现象**：琥珀 `#FF9E1A`、品红 `#E040A0`、青 `#2FB4C9` 三种材质，渲出来都是白。
- **根因**：观变换确定后，自发光强度决定能否保色。AgX 是"高亮度被去饱和"，
  Standard 是"线性值 ≥ 1 就削顶成白"——**强度过高在 Standard 下同样保不住颜色**。
- **修复**：定位图的 `emission_strength` 降到 0.4；一并补上 `scale_lights`
  （此前只减了单件图证的灯光，定位图漏减，灰底幽灵被灯光冲白）。
- **预防**：这个旋钮要**两头都试**——AgX 下怕去饱和、Standard 下怕过曝；
  纯色自发光建议从 0.4 起调，且**必须看图定值**（数值上无从判断）。

**同期记入的版本相关 API**：Blender 5.0 要做半透明（灰底幽灵），需要三处同时给——

```python
bsdf.inputs["Alpha"].default_value = 0.5     # Principled 的 Alpha
mat.blend_method = "BLEND"                   # 旧属性名，5.0 仍在
mat.surface_render_method = "BLENDED"        # 4.2+ 新属性名
```

两个混合属性**都设**（`hasattr` 判断后设），避免版本漂移时静默渲成不透明。
漏设的表现是"半透明物体看起来完全不透明"，不会报错。
