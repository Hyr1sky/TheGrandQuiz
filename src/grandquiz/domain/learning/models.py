"""学习领域模型——纯 pydantic 数据结构（LearningResource / KnowledgeItem）。

两条确定性纪律在此落地（否则 replay 与跨轮次记账永远对不齐）：

- **本模块不 import kernel**：领域模型不依赖 runtime（分层守卫的对偶——kernel 才是禁 import
  domain 的一方）。领域事件另经 kernel 的 ``emit()`` 上脊柱，模型自身与 runtime 解耦。
- **不 import uuid / time / random / datetime**：ID 全走 ``derive_id`` 的稳定 hash 派生（决策 1），
  且模型**无任何时间戳字段**（决策 2）——创建 / 深读 / 答题的时序信息来自事件流的 ``seq`` / ``ts``
  （注入时钟），模型不存 ``created_at``。

ID 派生约定（工厂在构造点保证确定性，调用方拿不到手写随机 id）：
``resource_id = derive_id(locator)``（locator-addressed，同一规范 locator 全局唯一，ADR-0007）；
``item_id = derive_id(resource_id, item_fingerprint)``，其中 fingerprint 来自规范化概念名 + 稳定排序的
evidence 引文。Reader 重排不改变 KnowledgeItem 身份。
"""

import hashlib
import unicodedata
from collections.abc import Iterable
from typing import Annotated, Literal, Self

from pydantic import BaseModel, BeforeValidator, Field, StringConstraints

# 非空字符串（去首尾空白后至少 1 字符）：概念名 / 摘要 / 证据引文的硬约束。
# 决策 3 的门原本只挡"evidence 列表为空"，不挡"引文为空串"——空串 quote/concept/summary
# 能铸出空白幽灵 item 直达 store。此约束把"无内容"从构造点一并挡住（strip 后为空也拒）。
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _as_str_list(value: object) -> object:
    # 真机 LLM 常把"只有一条"的列表写成裸字符串——宽容纳成单元素列表（Postel 定律），
    # 使结构化输出契约耐得住这个最常见的偏差。非空 / 逐字锚定的门在其后仍照常把关；
    # 其它非列表输入（None / dict 等）照旧交给 pydantic 报错 → ModelRetry。
    return [value] if isinstance(value, str) else value


# 出题 / 判卷 LLM 的 cited_evidence 字段共用：list[str]，但裸字符串会被宽容纳成单元素列表。
CitedEvidence = Annotated[list[str], BeforeValidator(_as_str_list)]


def ungrounded_citations(cited: Iterable[str], evidence_quotes: Iterable[str]) -> list[str]:
    """返回未锚定到真实证据的引文——防"幽灵引文"（LLM 引伪造原文蒙混）。

    一条引文算"锚定"当且仅当 strip 后非空、且**是某条 evidence.quote 的子串**
    （逐字出现在真实原文里）。用子串而非逐字全等：Reader 常抽较长证据段，而出题 /
    判卷模型倾向只引其中一句短句——那仍是真实原文、应放行；纯全等会把这类合法子串误判
    成幽灵引文（真机 dogfood 踩过的坑）。空 / 纯空白引文视作未锚定。返回未锚定引文列表。
    """
    quotes = list(evidence_quotes)
    return [c for c in cited if not (c.strip() and any(c.strip() in q for q in quotes))]


def derive_id(*parts: str) -> str:
    """确定性 ID 派生：只用稳定输入 hash，禁止 uuid / time / random（决策 1）。

    以 NUL（``\\x00``）连接各 ``parts`` 再取 sha256，返回 hexdigest 前 16 位。
    NUL 分隔避免 ``("a", "bc")`` 与 ``("ab", "c")`` 拼接后撞同一 key；同一输入恒得同一 id，
    replay 因此对得齐。16 位十六进制（64 bit）对 N=1 用户量的资源 / item 规模碰撞概率可忽略。

    入参先做 Unicode **NFC 归一化**再 hash：中文 / 多来源文本（粘贴 vs 输入法）可能是不同规范化
    形式（NFC 单码点 vs NFD 组合字符），字节不同但语义同一。不归一化会让"同一"标题 / URL 派生出
    不同 id，破坏概念同一性与去重。注意这不是 replay 破坏点（重放的是同一串字节、仍确定），
    而是跨会话录入的稳健性。
    """
    normalized = [unicodedata.normalize("NFC", part) for part in parts]
    joined = "\x00".join(normalized)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return digest[:16]


class Evidence(BaseModel):
    """KnowledgeItem 的证据：一段原文引文 + 可选结构定位符。

    ``locator`` 携 section_path / 锚点，MVP 恒留 None——字段 / 形状第一天就在，是 ADR-0002
    资源内概念树的前向兼容缝（grounding 与二期资源内边的地基），此刻不填也不抽取。
    """

    quote: NonEmptyStr
    locator: str | None = None


def _identity_text(value: str) -> str:
    """Normalize model-authored identity text without erasing punctuation."""
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def _item_fingerprint(concept: str, evidence: Iterable[Evidence]) -> str:
    quotes = sorted({_identity_text(item.quote) for item in evidence})
    return derive_id(_identity_text(concept), *quotes)


class KnowledgeItem(BaseModel):
    """深读一个资源产出的最小知识单元，资源内唯一——概念同一性的边界（ADR-0002）。

    ``evidence`` 非空是模型级硬校验门（决策 3）：无证据的 item 不许存在，从构造点挡住幽灵
    知识点污染考核循环。``concept_key`` 为二期跨资源归并预留，MVP 恒 None（ADR-0002）。
    无时间戳字段（决策 2）。
    """

    item_id: str
    resource_id: str
    concept: NonEmptyStr
    summary: NonEmptyStr
    evidence: list[Evidence] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    concept_key: str | None = None

    @classmethod
    def create(
        cls,
        *,
        resource_id: str,
        concept: str,
        summary: str,
        evidence: list[Evidence],
        confidence: float,
    ) -> Self:
        """按资源 + 概念证据指纹生成不受 Reader 顺序影响的稳定 ID。"""
        return cls(
            item_id=derive_id(resource_id, _item_fingerprint(concept, evidence)),
            resource_id=resource_id,
            concept=concept,
            summary=summary,
            evidence=evidence,
            confidence=confidence,
        )


class LearningResource(BaseModel):
    """全局 KB 里的一个学习资源（待深读 / 已深读 / 深读失败），按 locator 标识（ADR-0007）。

    ``resource_id = derive_id(url)``——同 URL 全局唯一，不再挂在某个 ``LearningTask`` 下
    （``LearningTask`` 已消解）；重 ingest 同一 URL → 同 resource_id → ``INSERT OR REPLACE`` 天然
    去重。``topic`` = 资源级软标签（"这份材料讲什么"的一句话，Reader 抽，本 slice 先建列、留空，
    S3 填），是目录式 scope 清单的人类可读来源。``trusted`` 默认 False——抓取的网页 / GitHub 内容
    是不可信输入（注入防护，深读前不得当可信）；``status`` 深读失败 → ``"failed"``，不产生幽灵
    item（eval case 7）。无时间戳字段（决策 2）：创建 / 深读时序来自事件流。
    """

    resource_id: str
    url: str
    raw_content: str | None = None
    content_hash: str | None = None
    trusted: bool = False
    status: Literal["pending", "read", "failed"] = "pending"
    topic: str | None = None

    @classmethod
    def create(cls, *, url: str) -> Self:
        """工厂：``resource_id = derive_id(url)``（内容寻址，同 URL 全局唯一）。"""
        return cls(resource_id=derive_id(url), url=url)
