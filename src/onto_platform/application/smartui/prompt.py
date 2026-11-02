"""Prompt packs for Smart-UI generation.

A prompt pack contributes the model contract for a generation request.  The
default pack is the Blueprint v1.0/v1.1 prompt that shipped with Studio; a
workspace/domain pack can decorate it later without editing the pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .catalog import GOALS, PERSPECTIVES


@dataclass(frozen=True)
class PromptRequest:
    pack: dict[str, Any]
    question: str
    base_blueprint: dict[str, Any] | None = None
    instruction: str = ""


class DefaultPromptPack:
    """The built-in contract that pins the model to schema v1.0 + options v1.1."""

    id = "onto.smartui.prompt.default"
    version = "1.0.0"

    def render(self, request: PromptRequest) -> tuple[str, str]:
        system = (
            "你是业务智能视图规划器。根据工作空间的本体与可用能力，输出一个"
            " Blueprint v1.0 JSON。只允许引用给定对象、视角与动作，不得输出坐标、"
            "颜色、CSS、脚本、表达式或 HTML。只输出 JSON，不要解释。"
        )
        schema_hint = {
            "name": "PascalCase 应用名，3-64 字符",
            "displayName": "中文显示名，≤64 字符",
            "description": "一句话说明，≤200 字符",
            "context": {
                "object": "必须取自 objects[].name",
                "goal": "枚举: " + " | ".join(GOALS),
                "focus": {"key": "对象属性名（可选）", "value": "实例值（可选）"},
            },
            "views": [
                {"perspective": "枚举: " + " | ".join(PERSPECTIVES),
                 "title": "可选中文标题",
                 "options": {
                     "chartType": "可选 bar|line|pie|table；table 仅用于 statistics",
                     "display": "可选 panel|table（statistics）；"
                                "gis|scene25d|twin3d（仅 location 空间呈现）",
                     "groupBy": "可选对象属性名",
                     "metric": {"field": "对象属性名", "agg": "sum|avg|count|min|max"},
                     "sort": {"field": "对象属性名", "direction": "asc|desc"},
                     "limit": "可选 1-200 的整数",
                     "columns": "可选对象属性名数组（table 列）",
                     "filters": [{"field": "对象属性名",
                                  "op": "eq|ne|gt|gte|lt|lte|contains|in",
                                  "value": "字面量或 -7d 这类相对值"}],
                 }},
            ],
            "actions": ["只能取自 actions[].key"],
            "confidence": "0-1 的数字",
        }
        constraints = [
            "views 非空且 ≤8 个，perspective 不重复",
            "只使用 objects[].perspectives 中出现的视角",
            "actions 只能选当前角色可执行的已发布能力",
            "context.object 优先取 queryRoots；若选择 dimension，平台会解析到 resolvedQueryRoot",
            "没有合适焦点时省略 focus",
            "需要列表/筛选/排序/取前 N 条时，在对应 view 的 options 中表达，不要编造新视角",
            "options 的 groupBy/sort.field/columns/filters[].field 必须是该对象属性；limit ≤ 200",
            "table 形态只用于 statistics 视角；不得输出坐标、颜色、CSS、脚本或 expressions",
            "gis/scene25d/twin3d 只用于 location 视角；heightField 必须是该对象的数值属性，"
            "其余空间选项只能使用 schema 中声明的枚举，平台会剪除非法值",
            "若 context_pack.focus 存在且用户未明确要求别的实例，focus 应沿用该对象与值",
        ]
        workspace_pack = (request.pack or {}).get("workspacePack")
        if isinstance(workspace_pack, dict):
            name = str(workspace_pack.get("name") or workspace_pack.get("id") or "")
            if name:
                constraints.append(f"当前工作空间绑定 Pack：{name}；遵循其视角与预设")
            perspectives = workspace_pack.get("perspectives") or []
            if perspectives:
                constraints.append(
                    "优先使用 Pack 声明的视角：" + " | ".join(
                        str(item) for item in perspectives))
            if workspace_pack.get("ontologyProfile"):
                constraints.append(
                    "本体配置档：" + str(workspace_pack["ontologyProfile"]))
        user = json.dumps({
            "context_pack": request.pack,
            "user_request": request.question,
            "output_schema": schema_hint,
            "constraints": constraints,
            **({"previous_blueprint": request.base_blueprint}
               if request.base_blueprint else {}),
        }, ensure_ascii=False)
        if request.instruction:
            user += f"\n修改要求：{request.instruction}"
        return system, user
