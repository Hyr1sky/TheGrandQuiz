"""学习领域模型测试——缝 2 确定性核心（先红后绿）。

被测不变量全是 eval 命门：确定性 ID 派生（决策 1）、证据非空硬校验门（决策 3）、
confidence 边界、序列化往返 + JSON-able（进 event payload 的前提），
以及"领域模型不 import kernel / 无非确定性来源"这条分层 + 确定性守卫。
"""

import ast
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

import grandquiz.domain.learning.events as events_mod
import grandquiz.domain.learning.models as models_mod
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.models import (
    Evidence,
    KnowledgeItem,
    LearningResource,
    derive_id,
    ungrounded_citations,
)


def _one_evidence() -> list[Evidence]:
    return [Evidence(quote="闭包捕获的是变量而非值")]


# --- 引文锚定门（ungrounded_citations，防幽灵引文）-------------------------------

_LONG_QUOTE = "闭包捕获的是变量而非值，因此循环里注册的回调共享同一个 i"


def test_ungrounded_citations_accepts_exact_quote() -> None:
    assert ungrounded_citations([_LONG_QUOTE], [_LONG_QUOTE]) == []


def test_ungrounded_citations_accepts_substring() -> None:
    # 核心放宽：出题 / 判卷只引长证据里一句短句 → 是子串 → 锚定成立（真机 dogfood 坑）。
    assert ungrounded_citations(["捕获的是变量"], [_LONG_QUOTE]) == []


def test_ungrounded_citations_rejects_fabricated() -> None:
    # 伪造的"原文"不是任何证据的子串 → 判为未锚定（幽灵引文）。
    assert ungrounded_citations(["这句原文根本不存在"], [_LONG_QUOTE]) == ["这句原文根本不存在"]


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_ungrounded_citations_rejects_blank(blank: str) -> None:
    # 空 / 纯空白引文视作未锚定——挡"引空串蒙混"（空串是任何字符串的子串，须显式拒）。
    assert ungrounded_citations([blank], [_LONG_QUOTE]) == [blank]


def test_ungrounded_citations_reports_only_the_ungrounded() -> None:
    # 混合输入：只回未锚定的那条，锚定的（子串）放行。
    cited = ["捕获的是变量", "凭空捏造的原文"]
    assert ungrounded_citations(cited, [_LONG_QUOTE]) == ["凭空捏造的原文"]


# --- derive_id 确定性 -------------------------------------------------------


def test_derive_id_is_deterministic_for_same_input() -> None:
    assert derive_id("React") == derive_id("React")
    assert derive_id("a", "b") == derive_id("a", "b")


def test_derive_id_differs_for_different_input() -> None:
    assert derive_id("React") != derive_id("Vue")
    # NUL 分隔避免 ('a','bc') 与 ('ab','c') 拼接后撞 key
    assert derive_id("a", "bc") != derive_id("ab", "c")


def test_derive_id_returns_16_hex_chars() -> None:
    out = derive_id("React")
    assert len(out) == 16
    assert all(c in "0123456789abcdef" for c in out)


def test_derive_id_normalizes_unicode_nfc() -> None:
    # 源文件纯 ASCII、无歧义：用码点显式构造同一逻辑串的两种 Unicode 规范化形式。
    composed = "caf" + chr(0x00E9)  # 预组合：e-acute 单码点 (NFC)
    decomposed = "cafe" + chr(0x0301)  # 分解：e + 组合重音 (NFD)
    assert composed != decomposed  # 两种字节形式确实不同
    # NFC 归一化后派生同一 id（跨会话录入稳健性，非 replay 破坏点）
    assert derive_id(composed) == derive_id(decomposed)


# --- 工厂 ID 派生 -----------------------------------------------------------


def test_resource_create_id_is_content_addressed_from_url() -> None:
    # 内容寻址（ADR-0005）：resource_id = derive_id(url)，同 URL 全局唯一（不再有 task_id 入参）。
    url = "https://example.com/a"
    r1 = LearningResource.create(url=url)
    r2 = LearningResource.create(url=url)
    assert r1.resource_id == r2.resource_id == derive_id(url)
    other = LearningResource.create(url="https://example.com/b")
    assert other.resource_id != r1.resource_id
    # 抓取内容默认不可信、状态默认 pending（注入防护 + 不产生幽灵 item）；topic 软标签默认 None。
    assert r1.trusted is False
    assert r1.status == "pending"
    assert r1.topic is None


def test_resource_topic_round_trips_when_set() -> None:
    # topic 软标签（S3 由 Reader 填）：带值时原样往返，不只测默认 None。
    resource = LearningResource.create(url="https://example.com/a").model_copy(
        update={"topic": "代理通信协议"}
    )
    assert LearningResource(**resource.model_dump()).topic == "代理通信协议"


def test_item_create_id_is_resource_scoped_and_zero_padded() -> None:
    resource_id = derive_id("t", "https://example.com/a")
    item = KnowledgeItem.create(
        resource_id=resource_id,
        index=1,
        concept="闭包",
        summary="函数捕获定义时的作用域",
        evidence=_one_evidence(),
        confidence=0.9,
    )
    assert item.item_id == f"{resource_id}#001"
    assert item.resource_id == resource_id
    # concept_key 二期跨资源归并预留，MVP 恒 None
    assert item.concept_key is None


@pytest.mark.parametrize("index", [0, 5, 42, 999])
def test_item_id_is_zero_padded_to_three_digits(index: int) -> None:
    item = KnowledgeItem.create(
        resource_id="r",
        index=index,
        concept="c",
        summary="s",
        evidence=_one_evidence(),
        confidence=0.5,
    )
    assert item.item_id == f"r#{index:03d}"


# --- evidence 非空硬校验（决策 3）------------------------------------------


def test_knowledge_item_rejects_empty_evidence() -> None:
    with pytest.raises(ValidationError):
        KnowledgeItem(
            item_id="r#000",
            resource_id="r",
            concept="闭包",
            summary="摘要",
            evidence=[],
            confidence=0.5,
        )


def test_knowledge_item_accepts_one_evidence() -> None:
    item = KnowledgeItem(
        item_id="r#000",
        resource_id="r",
        concept="闭包",
        summary="摘要",
        evidence=_one_evidence(),
        confidence=0.5,
    )
    assert len(item.evidence) == 1
    assert item.evidence[0].quote == "闭包捕获的是变量而非值"
    assert item.evidence[0].locator is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_knowledge_item_rejects_blank_quote(blank: str) -> None:
    # 决策 3 强化：空串 / 纯空白引文也是"无证据"，须被 NonEmptyStr 挡下（不只挡空 evidence 列表）。
    with pytest.raises(ValidationError):
        KnowledgeItem.model_validate(
            {
                "item_id": "r#000",
                "resource_id": "r",
                "concept": "闭包",
                "summary": "摘要",
                "evidence": [{"quote": blank}],
                "confidence": 0.5,
            }
        )


@pytest.mark.parametrize("field", ["concept", "summary"])
def test_knowledge_item_rejects_blank_concept_or_summary(field: str) -> None:
    # 空串 / 纯空白 concept·summary 同样是空白幽灵 item，须被挡下。
    data: dict[str, object] = {
        "item_id": "r#000",
        "resource_id": "r",
        "concept": "闭包",
        "summary": "摘要",
        "evidence": [{"quote": "q"}],
        "confidence": 0.5,
    }
    data[field] = "   "
    with pytest.raises(ValidationError):
        KnowledgeItem.model_validate(data)


# --- confidence 边界 --------------------------------------------------------


@pytest.mark.parametrize("bad", [1.5, -0.1])
def test_knowledge_item_rejects_out_of_range_confidence(bad: float) -> None:
    with pytest.raises(ValidationError):
        KnowledgeItem(
            item_id="r#000",
            resource_id="r",
            concept="闭包",
            summary="摘要",
            evidence=_one_evidence(),
            confidence=bad,
        )


@pytest.mark.parametrize("good", [0.0, 1.0])
def test_knowledge_item_accepts_boundary_confidence(good: float) -> None:
    item = KnowledgeItem(
        item_id="r#000",
        resource_id="r",
        concept="闭包",
        summary="摘要",
        evidence=_one_evidence(),
        confidence=good,
    )
    assert item.confidence == good


# --- status Literal 校验门 --------------------------------------------------


def test_resource_rejects_invalid_status() -> None:
    # 非法状态必须被 Literal 挡下（用 model_validate 喂 Any，避免 pyright 提前拦下测试数据）
    with pytest.raises(ValidationError):
        LearningResource.model_validate(
            {"resource_id": "r", "url": "u", "trusted": False, "status": "bogus"}
        )


# --- 序列化往返 + JSON-able（进 event payload 的前提）-----------------------


def test_knowledge_item_round_trips_through_model_dump() -> None:
    item = KnowledgeItem.create(
        resource_id="r",
        index=0,
        concept="闭包",
        summary="函数捕获定义时的作用域",
        evidence=[Evidence(quote="片段一"), Evidence(quote="片段二")],
        confidence=0.75,
    )
    assert KnowledgeItem(**item.model_dump()) == item


def test_resource_round_trips_through_model_dump() -> None:
    resource = LearningResource.create(url="https://example.com/a")
    assert LearningResource(**resource.model_dump()) == resource


def test_evidence_locator_round_trips_when_present() -> None:
    # 前向兼容缝：locator 带值时（section_path/锚点）也须原样往返，不只测 None
    ev = Evidence(quote="片段", locator="§2.1#anchor")
    assert Evidence(**ev.model_dump()).locator == "§2.1#anchor"


def _sample_models() -> list[BaseModel]:
    return [
        LearningResource.create(url="https://example.com/a"),
        Evidence(quote="片段", locator="§1.2"),
        KnowledgeItem.create(
            resource_id="r",
            index=0,
            concept="闭包",
            summary="摘要",
            evidence=_one_evidence(),
            confidence=0.9,
        ),
    ]


@pytest.mark.parametrize("model", _sample_models())
def test_all_payload_models_are_json_able(model: BaseModel) -> None:
    # 每个进 event payload 的模型都须 JSON-able（决策 4：payload=model_dump()），非只 Item。
    # dump(python)==dump(json) 证明无字段需 JSON 强转——挡住日后混进 datetime/bytes/Enum。
    assert model.model_dump(mode="python") == model.model_dump(mode="json")
    json.dumps(model.model_dump(), ensure_ascii=False)  # 不炸即 JSON-able


# --- 字段集合钉死：决策 2（无时间戳）的直接守卫 ----------------------------


def test_model_field_sets_are_pinned() -> None:
    # import 守卫挡不住 `created_at: float = 0.0`（不 import datetime 的时间戳字段）。
    # 钉死字段集合才是决策 2 的直接守卫：偷加/漏删字段在此失败，逼一次自觉的 schema 变更。
    assert set(Evidence.model_fields) == {"quote", "locator"}
    assert set(KnowledgeItem.model_fields) == {
        "item_id",
        "resource_id",
        "concept",
        "summary",
        "evidence",
        "confidence",
        "concept_key",
    }
    assert set(LearningResource.model_fields) == {
        "resource_id",
        "url",
        "raw_content",
        "content_hash",
        "trusted",
        "status",
        "topic",
    }


# --- 分层 + 确定性守卫：模型模块不 import kernel / 无非确定性来源 -----------


def _imported_modules(module_file: str) -> set[str]:
    tree = ast.parse(Path(module_file).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def test_models_module_has_no_kernel_or_nondeterministic_imports() -> None:
    imported = _imported_modules(models_mod.__file__)
    # 确定性走注入：ID 派生只用稳定 hash，禁 uuid/time/random/datetime（决策 1、2）
    forbidden = {"uuid", "time", "random", "datetime"}
    assert not {m.split(".")[0] for m in imported} & forbidden
    # 分层守卫的对偶：领域模型不依赖 runtime（kernel 才是禁 import domain 的一方）
    assert not any(m.startswith("grandquiz.kernel") for m in imported)


def test_events_module_is_kernel_free() -> None:
    # 领域事件只是命名空间字符串常量，经 kernel emit() 上脊柱，本身不 import kernel
    imported = _imported_modules(events_mod.__file__)
    assert not any(m.startswith("grandquiz.kernel") for m in imported)


def test_learning_event_constants_are_namespaced() -> None:
    for value in (
        LearningEvent.RESOURCE_CREATED,
        LearningEvent.RESOURCE_FETCH_FAILED,
        LearningEvent.ITEMS_EXTRACTED,
        LearningEvent.RESOURCE_APPROVED,
        LearningEvent.ITEM_CREATED,
    ):
        assert value.startswith("learning.")
