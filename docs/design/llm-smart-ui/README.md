# Smart UI Studio 原型稿

配套方案：[`docs/smart-ui-llm-generation.md`](../../smart-ui-llm-generation.md)（第 8 节界面方案）；
无大模型场景见 [`rule-driven-generation.md`](rule-driven-generation.md)；
插件化落地记录见 [`pluginization.md`](pluginization.md)。

## 文件

| 文件 | 说明 |
|---|---|
| `01-studio.svg` | 生成台·结果态：左描述/约束，中沙箱预览（真实数据），右生成轨迹与平台裁决 |
| `02-iterate.svg` | 多轮修改：对话与改动清单、v4 差异预览、版本条、发布抽屉 |
| `03-library.svg` | 生成记录与版本审计：列表、详情、回滚、质量与成本 |
| `04-architecture.svg` | 生成链路：LLM 提议 → 平台裁决 → 确定性编译 → 预览 → 资产化 |
| `architecture-current.svg` | 当前插件化架构：微内核、8 类贡献点、Pack / 执行锁 / 描述符 / renderer |
| `architecture-evolution.svg` | 可演化目标架构：已落地基线与后续隔离 / 独立 renderer 前端包 |
| `build_mockups.py` | 可复现生成器（纯 Python + PIL 文本度量），产物即 4 个 SVG |
| `audit_dom.py` | 浏览器真实 bbox 审计（文本重叠、越界、面板包含） |
| `render_png.py` | 用 Edge/Chromium 渲染 PNG，检查 console 错误 |
| `*.png` | 渲染预览（1600×1000 / 1600×760） |

## 重新生成与校验

```powershell
python docs/design/llm-smart-ui/build_mockups.py   # 出 SVG + 静态几何/文本审计，exit 1 表示不合格
python docs/design/llm-smart-ui/audit_dom.py       # 浏览器真实字体度量审计
python docs/design/llm-smart-ui/render_png.py      # 渲染 PNG，要求 console_issues 为空
```

审计口径：

- `build_mockups.py`：每段文本声明 `maxw`，按 `Microsoft YaHei / SimSun / Consolas` 真实字宽校验；卡片级矩形做重叠检测；所有元素限画布内。
- `audit_dom.py`：Chromium `getBBox()` 复核；文本两两重叠面积 > 2px² 报错；文本必须落在所属卡片内（页头/侧栏/工具条等自由区除外）。
- `render_png.py`：页面无 JS 错误、SVG 尺寸与 `viewBox` 一致。

当前结果：4 张图 0 个审计错误，0 条 console 错误。

## 审核要点

1. **三栏比例**（340 / 664 / 272，1440+ 视口）是否适合“描述 → 预览 → 裁决”的生成节奏。
2. **预览是否足够“真”**：筛选器、按站点分组、地图、KPI、明细表的组合是否符合值班站长预期。
3. **裁决面板**是否把“LLM 只提议”讲清楚：Schema / 组件白名单 / 数据范围 / 动作权限 / 预算五行。
4. **发布语义**：发布到应用、不可原地编辑、需克隆后发布——与现有应用生命周期一致。
5. **M1/M2 边界**：原型按 M2 目标态绘制。M1 交付时预览只含现有 8 类运行时组件，筛选器与分组表格在 M2 随 Blueprint v1.1 一起交付。

## 字体与移植

原型按 Windows 字体栈绘制（Microsoft YaHei / SimSun / Consolas）。若在 macOS/Linux 打开，字形宽度会变化；改图后请重新跑 `audit_dom.py`，不要只看静态审计。

## 与运行时的关系

预览中的暖色画布、VIBE 顶栏、角色/动作计数来自 `vibe-可视化engine/runtime-web` 的真实样式与文案；控制台外壳沿用 `frontend/src/styles.css` 的暖单色 tokens（`--bg / --surface / --ink / --line`）与 1px 结构线。原型是设计稿，不引用真实组件代码。
