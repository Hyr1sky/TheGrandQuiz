"""考核范围的 fail-closed 三态契约。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from grandquiz.domain.learning.models import NonEmptyStr


class AllScope(BaseModel):
    """用户没有指定材料，允许从全库选题。"""

    model_config = ConfigDict(frozen=True)
    mode: Literal["all"] = "all"


class SelectedScope(BaseModel):
    """用户点名材料且已解析为至少一个精确 resource_id。"""

    model_config = ConfigDict(frozen=True)
    mode: Literal["selected"] = "selected"
    resource_ids: list[NonEmptyStr] = Field(min_length=1)


class UnresolvedScope(BaseModel):
    """用户点名了材料，但目录中无法解析出 resource_id。"""

    model_config = ConfigDict(frozen=True)
    mode: Literal["unresolved"] = "unresolved"
    requested_label: NonEmptyStr


QuizScope = Annotated[AllScope | SelectedScope | UnresolvedScope, Field(discriminator="mode")]
ALL_SCOPE = AllScope()
