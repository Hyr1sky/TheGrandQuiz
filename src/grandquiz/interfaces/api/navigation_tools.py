"""导航工具——仅 Web API 层可见的面板切换工具。

住 ``interfaces/api/`` 层（不在 ``domain/`` 或 ``kernel/``）。CLI composition 不调用
``register_navigation_tools``，CLI 与 eval 看不到这些工具。

handler 不执行重逻辑——校验参数 + 通过 ``ToolContext`` 发 ``navigation.requested`` 事件 +
返回确认文本给 LLM。前端监听投影后的 ``chat.navigation`` UI 事件驱动面板切换。
"""

from typing import Any

from pydantic import BaseModel, Field

from grandquiz.kernel.tools import Tool, ToolContext, ToolRegistry

# ---- 事件类型常量 ---- #

NAVIGATION_REQUESTED = "navigation.requested"

# ---- start_assessment 工具 ---- #


class _StartAssessmentParams(BaseModel):
    resource_id: str = Field(description="要考核的材料 resource_id")
    rounds: int = Field(default=3, ge=1, le=20, description="考核题目数量")
    question_type: str | None = Field(
        default=None,
        description="题型（'选择题' 或 '简答题'），不填则自适应",
    )


async def _start_assessment_handler(params: _StartAssessmentParams, ctx: ToolContext) -> str:
    payload: dict[str, Any] = {
        "target": "assessment",
        "params": {
            "resource_id": params.resource_id,
            "rounds": params.rounds,
            "question_type": params.question_type,
        },
    }
    ctx.emitter.emit(NAVIGATION_REQUESTED, payload=payload)
    return "已为用户启动考核，考核将在工作面板进行。"


# ---- open_article 工具 ---- #


class _OpenArticleParams(BaseModel):
    resource_id: str = Field(description="要阅读的材料 resource_id")


async def _open_article_handler(params: _OpenArticleParams, ctx: ToolContext) -> str:
    payload: dict[str, Any] = {
        "target": "reading",
        "params": {"resource_id": params.resource_id},
    }
    ctx.emitter.emit(NAVIGATION_REQUESTED, payload=payload)
    return "已切换到文章阅读。"


# ---- 注册入口 ---- #


def register_navigation_tools(registry: ToolRegistry) -> None:
    """注册导航工具（``start_assessment`` / ``open_article``）。

    仅 Web API composition 调用；CLI / eval 不调用此函数。
    """
    registry.register(
        Tool(
            name="start_assessment",
            description=(
                "为用户启动考核——在工作面板打开试卷式考核界面。"
                "需要 resource_id（材料 ID）；可选 rounds（题数，默认 3）"
                "和 question_type（'选择题' / '简答题'，默认自适应）。"
            ),
            params=_StartAssessmentParams,
            handler=_start_assessment_handler,
            wants_context=True,
        )
    )
    registry.register(
        Tool(
            name="open_article",
            description="切换工作面板到文章阅读模式，显示指定材料的内容。",
            params=_OpenArticleParams,
            handler=_open_article_handler,
            wants_context=True,
        )
    )
