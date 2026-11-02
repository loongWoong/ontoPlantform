# AI视图插件化落地记录

本文件记录「一切皆插件」在 AI视图（LLM 生成链路）上的分阶段落地结果。
上游方案见 [`docs/smart-ui-llm-generation.md`](../../smart-ui-llm-generation.md)，
设计稿见 [`README.md`](README.md)。

## 不变量

1. **两个菜单互不影响**：`AI视图` 是模型生成链路；`智能视图` 是确定性
   `compile → OpenUI 程序 → sandbox` 链路。插件化只改前者，后者的入口、
   状态与产物不变。
2. **模型只提议**：生成器输出 `proposal`，必须经过裁决器白名单与 L4 确定性
   编译器，插件不能绕过 schema、本体、权限或发布审计。
3. **Blueprint v1.0 是稳定 ABI**：视图插件只裁决 `options` 与 payload，
   不改蓝图顶层结构；`schemaVersion` 仍由平台写入。
4. **治理留在内核**：角色、权限、审计、发布、编译、插件宿主与版本协商都是
   core；工作空间包只能是声明式数据，不能携带代码。
5. **前端无远程脚本**：UI slot 只接受随构建产物发布的组件；后端 manifest
   只负责过滤/排序可见 slot，不做远程加载。
6. **外部代码是显式边界**：外部插件默认关闭，必须同时打开开关、配置密钥并
   通过签名校验；当前加载后仍在宿主进程内执行，不是进程沙箱。

## 分层与贡献点

```text
interfaces/smart_ui.py          thin facade（确定性链路）
interfaces/smart_ui_studio.py   thin facade（生成链路）
        │
application/smartui/            Platform/Application 层
  bridge.py       snapshot / context / compile / meta
  studio.py       generation 状态机；委托 host 的 context / generator /
                  prompt / adjudicator / view / publisher
  host.py         PluginHost + 权限 / 执行锁 / UI manifest / 引擎描述符 / renderer
  builtin.py      18 个内置插件与默认宿主
  contracts.py    PluginManifest / PluginLockEntry / 版本与权限常量
  version.py      apiVersion 区间解析与兼容判定
  catalog.py      语义视角与上限
  context.py      ContextPack 贡献点（默认实现）
  prompt.py       PromptPack 贡献点（默认实现）
  generators.py   Generator 贡献点（deterministic / LLM）
  adjudication.py Adjudicator 链（sanitizer → default）
  views/          View 插件（table / chart / spatial: gis / scene25d / twin3d）
  renderers.py    Renderer 贡献点（viewType → 构建期组件，可版本化/禁用）
  publisher.py    Publisher 贡献点（默认实现）
  ui.py           UI 贡献点（aiview slots 与前端 fallback 描述）
  descriptors.py  EngineDescriptorRegistry（perspective/view/layout）
  external.py     签名本地包安装边界（opt-in）
  packs.py        WorkspacePack 声明式包 + workspace 绑定配置
        │
ports/smart_ui.py               Generator / PromptPack / Adjudicator / ViewPlugin 协议
```

贡献点：`context`、`generator`、`prompt`、`adjudicator`、`view`、`renderer`、
`publisher`、`ui`。语义视角（`identity / relationship / timeline / location /
statistics / state / rule / action`）仍是 core 语义；`table` / `chart` / `gis` /
`scene25d` / `twin3d` 的取数与选项由 view 插件负责，`viewType → component`
映射由 renderer 插件声明。三种空间形态共享 `location` 能力与同一份
`view_location` payload，只通过 `display` 选项选择表现，默认仍是 `map`/`GeoMap`。

## 阶段结果

| 阶段 | 内容 | 落点 |
|---|---|---|
| 0 | 插件契约、宿主、显式 facade、内置注册 | `contracts.py`、`host.py`、`builtin.py`、`ports/smart_ui.py` |
| 1 | generator / prompt / view / adjudicator 从 `studio` 拆成贡献点 | `generators.py`、`prompt.py`、`views/`、`adjudication.py` |
| 2 | `table` / `chart` 视图插件化；选项裁决与 payload 由插件接管 | `views/table.py`、`views/chart.py` |
| 3 | 裁决器链（sanitizer 先剪布局键，default 再做 schema/本体/动作白名单）；前端 UI slot 注册 AI视图 tabs | `adjudication.py`、`frontend/src/ui-slots.tsx`、`ai-view-slots.tsx` |
| 4 | 声明式 `WorkspacePack` 契约、`traffic` / `finance-lease` 示例、meta 暴露 | `packs.py`、`host.py` |
| 5 | `context` / `publisher` / `ui` 运行时实现；后端 manifest 过滤前端 tabs | `context.py`、`publisher.py`、`ui.py`、`builtin.py` |
| 6 | workspace 绑定：`smartui_config` + config API；生成请求携带 `pack`，prompt/context 注入；preset 兜底 | `packs.py`、`studio.py`、`interfaces/api.py` |
| 7 | apiVersion 区间、permissions 授权、执行锁、引擎描述符、renderer、回放、签名外部包 | `version.py`、`host.py`、`descriptors.py`、`renderers.py`、`external.py`、`studio.py` |
| 8 | GIS / 2.5D / 3D 孪生空间视图：`display` 变体、选项裁决、引擎编译、构建期组件与样式 | `views/spatial.py`、`builtin.py`、引擎 `capabilities.py`/`blueprint.py`/`emitter.py`、runtime-web `spatial.tsx` |

## ABI 与观测

`GET /api/workspaces/{wid}/smart-ui/meta` 新增/更新：

```json
{
  "plugins": [{"id": "...", "kind": "...", "order": 100, "enabled": true,
               "apiVersions": [">=smartui/v1 <smartui/v2"],
               "permissions": ["proposal.generate"]}],
  "pluginRegistryLock": [{"id": "...", "kind": "...", "version": "...",
                          "apiVersion": "smartui/v1"}],
  "pluginLock": [{"id": "...", "kind": "...", "version": "...",
                  "apiVersion": "smartui/v1"}],
  "packs": [{"id": "onto.pack.traffic", "name": "高速运营", "version": "1.0.0"}],
  "activePack": {"id": "onto.pack.traffic", "...": "..."},
  "ui": {"slots": [{"id": "studio", "slot": "aiview.tabs", "order": 10}],
         "renderers": [{"viewType": "table", "component": "DataTable",
                        "pluginId": "onto.smartui.renderer.table",
                        "order": 10, "format": "table"}],
         "remoteScripts": false},
  "engineDescriptors": [{"perspective": "statistics", "viewType": "chart",
                         "component": "StatisticChart"}]
}
```

- `pluginRegistryLock` 是注册组合锁（向后兼容）；`pluginLock` 是**执行组合锁**，
  只包含有实现的插件，生成 trace / draft / 编译 artifact 使用它。
- `activePack` 来自 workspace 的 `smartui_config`；请求级 `pack_id` 可以覆盖，
  但只影响本次 generation。
- 生成 artifact / draft / trace / 发布 artifact 携带 `pack` 与 `pluginLock`；
  `GET /api/workspaces/{wid}/smart-ui/generations/{id}/replay` 用历史蓝图 +
  当前执行锁重编译并报告缺失插件，用于发现锁漂移。
- UI slot 仍由构建期 React registry 提供组件；后端 `ui.slots` 只决定可见性与
  排序，缺 manifest 时前端回退到全部构建期 tab。

### 空间视图（GIS / 2.5D / 3D）

`location` 仍由 core 推导，插件只接管表现选项。`display` 是唯一的选择键：

| display | viewType | 组件 | 主要选项 |
|---|---|---|---|
| （省略） | `map` | `GeoMap` | 默认轨迹地图 |
| `gis` | `gis` | `GISMap` | `basemap` / `layers` / `cluster` |
| `scene25d` | `scene_2_5d` | `Scene25D` | `heightField` / `showLabels` / `theme` |
| `twin3d` | `twin_3d` | `Twin3D` | `heightField` / `theme` |

三种变体共享 `view_location` 的同一份 payload；bridge 额外写入 `viewType`、
`display`、`style`，并按 `heightField`（缺省时取首个数值属性）给每个点补
`field`/`value`。组件全部是纯 SVG/CSS：不加载瓦片、远程脚本或 3D/WebGL 库。

## 版本、权限与信任

- **API 版本**：`HOST_API_VERSION = smartui/v1.2`，插件可用
  `>=smartui/v1 <smartui/v2` 这类区间；未写区间默认按 `v1.x` 兼容。不兼容的
  外部 manifest 在 `PluginHost.register(..., trusted=True)` 时被拒绝。
- **权限**：manifest 的 `permissions` 进入 `PLUGIN_PERMISSIONS` 白名单；
  `host.implementation(id, permission=...)` 与 `host.find(..., permission=...)`
  做授权。Studio 对 generator / adjudicator / view / prompt / publisher / context
  调用都带权限。Pack 的 `permissions` 进一步收窄 context 动作与可生成视角。
- **builtin / 外部**：非 `builtin` manifest 必须由受信加载器注册；普通
  `host.register()` 默认拒绝。外部插件安装见 `external.py`。
- **外部包**：目录内 `manifest.json` + `plugin.py`，HMAC-SHA256 签名覆盖
  canonical manifest 与源码字节。必须设置：
  `ONTO_SMARTUI_EXTERNAL_PLUGINS=1`、`ONTO_SMARTUI_PLUGIN_SECRET`。
  校验通过后仍在进程内 `exec`/import，**不是**进程隔离，也不提供 SBOM。

## 架构图

- 当前：`Phase 0–8` 已落地的分层、贡献点、Pack 绑定、运行链路与空间视图
  （[architecture-current.svg](architecture-current.svg)）
- 演化：在保持 ABI / 治理不变的前提下，补外部**进程隔离**、独立 renderer
  bundle、成本遥测等运营能力的
  [architecture-evolution.svg](architecture-evolution.svg)

## 完成度复核

| 能力 | 状态 | 判据 / 剩余缺口 |
|---|---|---|
| 微内核与 manifest / host / registry lock | 已完成 | `contracts.py`、`host.py`、`builtin.py`；18 个内置 manifest |
| 生成 / 提示词 / 裁决 / 视图贡献点 | 已完成 | `studio` 通过 host 解析；sanitizer(10) → default(100) 顺序由测试锁定 |
| context / publisher / ui 贡献点 | 已完成 | `DefaultContextPack`、`DefaultPublisher`、`AIViewUIProvider` 均有实现；Studio 已走插件路径 |
| 视图 / 取数插件 | 已完成（平台侧） | `table` / `chart` 接管选项裁决与 payload；`gis` / `scene25d` / `twin3d` 共享 `view_location` 并裁决空间选项；引擎描述符已注册并进入产物 |
| 前端 UI 槽位 | 已完成（构建期 + 后端过滤） | `ui-slots.tsx` + `ai-view-slots.tsx`；后端 `ui.slots` 过滤/排序；无远程脚本 |
| 工作区包 | 已完成 | `smartui_config` 绑定；GET/POST config；`pack_id` 覆盖；prompt/context/preset 注入 |
| 版本协商 | 已完成 | `version.py` 支持 `>= <= > < = *` 与区间；默认 v1.x；不兼容拒绝 |
| 插件权限 | 已完成 | `permissions` 白名单 + 调用点授权；Pack 权限收窄动作/视角 |
| 插件锁 | 已完成 | 注册锁 + 执行锁；trace/draft/artifact/meta 携带；历史回放检测漂移 |
| 引擎描述符 | 已完成（view 维度） | `EngineDescriptorRegistry`；perspective/viewType/component/order |
| renderer 贡献点 | 已完成（平台侧） | `renderers.py`；`viewType → component` 由插件注册，进入 `ui.renderers` 与执行锁；组件仍随构建期 bundle 发布 |
| 外部代码插件 | 部分完成 | 签名本地包 + opt-in + 最小权限；**仍为进程内执行**，无进程隔离 / SBOM |
| 独立 renderer 前端包 | 未完成 | renderer 描述符已可注册/版本化/禁用；尚无独立可安装的远程前端包 |
| Pack 运营 UI | 未完成 | 后端 API/meta 已支持；Studio 尚未提供 pack 选择控件 |

结论：插件化骨架与可运营的核心闭环已经完成，当前可以作为稳定基线；下一阶段
应优先处理外部插件的进程隔离与供应链（SBOM / 依赖校验），再评估独立 renderer
前端包和 Pack 运营 UI。

## 验收证据

- 后端全量：`python -m pytest -q` → **497 passed**（含 runtime / pack / 版本 /
  权限 / renderer / 回放 / 外部签名 / config API 与空间视图测试）。
- Smart-UI 定向：`tests/test_smart_ui.py`、`test_smart_ui_studio.py`、
  `test_finance_lease_smart_ui.py`、`test_smart_ui_plugins.py`、
  `test_smart_ui_plugin_runtime.py`。
- 前端：`npm --prefix frontend test` → **46 passed**；
  `npm --prefix frontend run build` 通过。
- 引擎：`python -m pytest`（engine 目录）→ **31 passed**；新增 location 三种
  `display` 变体与默认 `GeoMap` 回归用例。
- 运行时：`runtime-web` 下 `npm test` → **19 passed**，`npm run typecheck` 通过；
  平台 `npm run build:platform` 产物已包含 `GISMap` / `Scene25D` / `Twin3D`。
- 插件面：18 个内置 manifest，18 个均有运行时 `implementation`；8 个贡献点
  全部有默认实现；3 个声明式工作空间包；5 个 renderer 映射。

## 暂未落地（有意留给下一阶段）

- 外部插件的**进程隔离 / 资源限额 / SBOM / 依赖校验**；当前仅签名 + opt-in +
  进程内执行。
- 独立 renderer 前端包的安装/下发；当前 renderer 描述符已可注册/版本化，
  但实际 React 组件仍随构建期 bundle 发布。
- Studio 的 Pack 选择与包管理 UI；当前通过 API 或 `smartui_config` 配置。
- 插件依赖求解、安装回滚、签名密钥轮换与吊销列表。
- 回放目前是“检测锁漂移 + 用历史蓝图重编译”，不是完整的时间旅行执行环境。
