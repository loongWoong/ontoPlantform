"""Built-in Smart-UI plugin manifests and implementations.

Every built-in contribution now has both a manifest and a runtime object.  The
core still owns authorization, ordering, compile and publish state; plugins only
contribute context, proposals, prompt contracts, adjudication, view options,
artifact shape and UI slot metadata.
"""

from __future__ import annotations

from .adjudication import DefaultProposalAdjudicator, SanitizerAdjudicator
from .context import DefaultContextPack
from .contracts import (
    PERMISSION_ARTIFACT_PUBLISH,
    PERMISSION_CONTEXT_READ,
    PERMISSION_PROPOSAL_ADJUDICATE,
    PERMISSION_PROPOSAL_GENERATE,
    PERMISSION_PROMPT_RENDER,
    PERMISSION_RENDER_COMPONENT,
    PERMISSION_UI_SLOT,
    PERMISSION_VIEW_RENDER,
    PluginManifest,
)
from .generators import DeterministicGenerator, LLMGenerator, RuleGenerator
from .host import PluginHost
from .packs import BUILTIN_PACKS
from .prompt import DefaultPromptPack
from .publisher import DefaultPublisher
from .renderers import (
    ChartRenderer,
    GISRenderer,
    Scene25DRenderer,
    TableRenderer,
    Twin3DRenderer,
)
from .ui import AIViewUIProvider
from .views.chart import ChartPlugin
from .views.spatial import GISPlugin, Scene25DPlugin, Twin3DPlugin
from .views.table import TablePlugin

BUILTIN_MANIFESTS: tuple[PluginManifest, ...] = (
    PluginManifest(
        id="onto.smartui.context.default",
        kind="context",
        version="1.1.0",
        summary="工作空间本体/焦点/权限上下文包",
        permissions=(PERMISSION_CONTEXT_READ,),
    ),
    PluginManifest(
        id="onto.smartui.generator.deterministic",
        kind="generator",
        summary="确定性 L3 规划 + L4 编译，模型不可用时兜底",
        permissions=(PERMISSION_PROPOSAL_GENERATE,),
    ),
    PluginManifest(
        id="onto.smartui.generator.rule",
        kind="generator",
        summary="无模型规则引擎：本体事实 + 规则包决策后交由 L4 编译",
        permissions=(PERMISSION_PROPOSAL_GENERATE,),
    ),
    PluginManifest(
        id="onto.smartui.generator.llm",
        kind="generator",
        summary="LLM 提议 Blueprint，平台裁决后编译",
        permissions=(PERMISSION_PROPOSAL_GENERATE,),
    ),
    PluginManifest(
        id="onto.smartui.prompt.default",
        kind="prompt",
        summary="Blueprint v1.0 + v1.1 options 的默认提示词契约",
        permissions=(PERMISSION_PROMPT_RENDER,),
    ),
    PluginManifest(
        id="onto.smartui.adjudicator.sanitizer",
        kind="adjudicator",
        summary="剪除模型输出的布局坐标/样式/脚本键",
        order=10,
        permissions=(PERMISSION_PROPOSAL_ADJUDICATE,),
    ),
    PluginManifest(
        id="onto.smartui.adjudicator.default",
        kind="adjudicator",
        summary="对象/视角/动作/选项白名单裁决",
        permissions=(PERMISSION_PROPOSAL_ADJUDICATE,),
    ),
    PluginManifest(
        id="onto.smartui.view.table",
        kind="view",
        summary="统计视角的表格呈现与取数",
        perspectives=("statistics",),
        view_type="table",
        component="DataTable",
        permissions=(PERMISSION_VIEW_RENDER,),
    ),
    PluginManifest(
        id="onto.smartui.view.chart",
        kind="view",
        summary="统计视角的柱/线/饼图呈现",
        perspectives=("statistics",),
        view_type="chart",
        component="StatisticChart",
        permissions=(PERMISSION_VIEW_RENDER,),
    ),
    PluginManifest(
        id="onto.smartui.view.gis",
        kind="view",
        summary="位置视角的分层 GIS 地图：底图、图层、聚合与围栏",
        perspectives=("location",),
        view_type="gis",
        component="GISMap",
        permissions=(PERMISSION_VIEW_RENDER,),
        metadata={"engine": {"perspectives": ["location"],
                            "viewType": "gis", "component": "GISMap"}},
    ),
    PluginManifest(
        id="onto.smartui.view.scene25d",
        kind="view",
        summary="位置视角的 2.5D 等轴场景：按业务指标拉伸对象",
        perspectives=("location",),
        view_type="scene_2_5d",
        component="Scene25D",
        permissions=(PERMISSION_VIEW_RENDER,),
        metadata={"engine": {"perspectives": ["location"],
                            "viewType": "scene_2_5d",
                            "component": "Scene25D"}},
    ),
    PluginManifest(
        id="onto.smartui.view.twin3d",
        kind="view",
        summary="位置视角的 3D 孪生场景：深度排序的对象块与联动选择",
        perspectives=("location",),
        view_type="twin_3d",
        component="Twin3D",
        permissions=(PERMISSION_VIEW_RENDER,),
        metadata={"engine": {"perspectives": ["location"],
                            "viewType": "twin_3d", "component": "Twin3D"}},
    ),
    PluginManifest(
        id="onto.smartui.renderer.table",
        kind="renderer",
        version="1.1.0",
        summary="表格 renderer 描述符（DataTable，构建期组件）",
        view_type="table",
        component="DataTable",
        order=10,
        permissions=(PERMISSION_RENDER_COMPONENT,),
        metadata={"format": "table"},
    ),
    PluginManifest(
        id="onto.smartui.renderer.chart",
        kind="renderer",
        version="1.1.0",
        summary="图表 renderer 描述符（StatisticChart，构建期组件）",
        view_type="chart",
        component="StatisticChart",
        order=20,
        permissions=(PERMISSION_RENDER_COMPONENT,),
        metadata={"format": "chart"},
    ),
    PluginManifest(
        id="onto.smartui.renderer.gis",
        kind="renderer",
        version="1.0.0",
        summary="GIS renderer 描述符（GISMap，构建期组件）",
        view_type="gis",
        component="GISMap",
        order=30,
        permissions=(PERMISSION_RENDER_COMPONENT,),
        metadata={"format": "gis"},
    ),
    PluginManifest(
        id="onto.smartui.renderer.scene25d",
        kind="renderer",
        version="1.0.0",
        summary="2.5D renderer 描述符（Scene25D，构建期组件）",
        view_type="scene_2_5d",
        component="Scene25D",
        order=40,
        permissions=(PERMISSION_RENDER_COMPONENT,),
        metadata={"format": "scene25d"},
    ),
    PluginManifest(
        id="onto.smartui.renderer.twin3d",
        kind="renderer",
        version="1.0.0",
        summary="3D 孪生 renderer 描述符（Twin3D，构建期组件）",
        view_type="twin_3d",
        component="Twin3D",
        order=50,
        permissions=(PERMISSION_RENDER_COMPONENT,),
        metadata={"format": "twin3d"},
    ),
    PluginManifest(
        id="onto.smartui.publisher.default",
        kind="publisher",
        version="1.1.0",
        summary="草稿、发布与只读快照",
        permissions=(PERMISSION_ARTIFACT_PUBLISH,),
    ),
    PluginManifest(
        id="onto.smartui.ui.aiview",
        kind="ui",
        version="1.1.0",
        summary="AI 视图的标签/面板槽位（构建期组件）",
        permissions=(PERMISSION_UI_SLOT,),
    ),
)


def build_default_host() -> PluginHost:
    host = PluginHost()
    implementations = {
        "onto.smartui.context.default": DefaultContextPack(),
        "onto.smartui.generator.deterministic": DeterministicGenerator(),
        "onto.smartui.generator.rule": RuleGenerator(),
        "onto.smartui.generator.llm": LLMGenerator(),
        "onto.smartui.prompt.default": DefaultPromptPack(),
        "onto.smartui.adjudicator.sanitizer": SanitizerAdjudicator(),
        "onto.smartui.adjudicator.default": DefaultProposalAdjudicator(),
        "onto.smartui.view.table": TablePlugin(),
        "onto.smartui.view.chart": ChartPlugin(),
        "onto.smartui.view.gis": GISPlugin(),
        "onto.smartui.view.scene25d": Scene25DPlugin(),
        "onto.smartui.view.twin3d": Twin3DPlugin(),
        "onto.smartui.renderer.table": TableRenderer(),
        "onto.smartui.renderer.chart": ChartRenderer(),
        "onto.smartui.renderer.gis": GISRenderer(),
        "onto.smartui.renderer.scene25d": Scene25DRenderer(),
        "onto.smartui.renderer.twin3d": Twin3DRenderer(),
        "onto.smartui.publisher.default": DefaultPublisher(),
        "onto.smartui.ui.aiview": AIViewUIProvider(),
    }
    for manifest in BUILTIN_MANIFESTS:
        host.register(manifest, implementations.get(manifest.id))
    for pack in BUILTIN_PACKS:
        host.register_pack(pack)
    return host
