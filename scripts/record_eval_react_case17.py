"""真实录制 case17：SearXNG 候选 → 用户选择 → ingest → 低质量页 fail closed。

运行：

    SEARXNG_URL=http://127.0.0.1:8080 uv run --env-file .env \
      python scripts/record_eval_react_case17.py

Search 走真实 SearXNG；Fetch 使用小型合成 MySQL 文档，避免把第三方整篇文章提交进测试 fixture。
Reader 与 ReAct 决策都走真实模型。只有规则门全绿时才保存 LLM 与 acquisition 两份 cassette。
"""

import asyncio
import hashlib
import os
from pathlib import Path

from grandquiz.domain.learning.ingest.acquisition_replay import (
    AcquisitionCassette,
    RecordingFetchSource,
    RecordingSearchProvider,
)
from grandquiz.domain.learning.ingest.fetch import DocumentQuality, FetchedDocument, FetchError
from grandquiz.domain.learning.ingest.web_search import SearXNGSearchProvider
from grandquiz.evals.graders.rules import grade_case17
from grandquiz.evals.harness import (
    CASE17_FETCH_FINGERPRINT,
    CASE17_FETCH_NORMALIZATION,
    CASE17_SEARCH_FINGERPRINT,
    Case,
    solve,
)
from grandquiz.providers.llm import OpenAICompatProvider
from grandquiz.providers.replay import Cassette, RecordingProvider

_LLM_FIXTURE = Path("tests/fixtures/eval_case17_web_acquisition_react.cassette.json")
_ACQUISITION_FIXTURE = Path("tests/fixtures/eval_case17_web_acquisition.cassette.json")
_GOOD_URL = "https://javaguide.cn/database/mysql/mysql-questions-01.html"
_BAD_URL = "https://example.com/login"
_CONTENT = """# MySQL 面试高频考点

## 索引与回表

InnoDB 的普通二级索引叶子节点保存主键值。查询列没有被二级索引覆盖时，需要先从二级索引取得主键，
再访问聚簇索引读取完整行，这个过程称为回表。覆盖索引可以直接从索引取得查询所需列，减少随机 I/O。

## 联合索引与最左前缀

联合索引按照索引列的声明顺序组织。查询条件从最左列开始连续匹配时，优化器更容易利用索引；跳过最左列
通常无法直接使用该联合索引的有序前缀。范围条件之后的列能否继续用于定位，需要结合具体执行计划判断。

## 事务隔离

读已提交在每条语句开始时建立 Read View，可重复读通常在事务第一次一致性读时建立 Read View。
InnoDB 通过 MVCC 与锁协作处理并发；当前读仍会读取最新版本并按需加锁，不能把快照读规则套到所有查询。

## 排查方法

面试中讨论慢查询时，应先用 EXPLAIN 查看访问类型、可能索引、实际选用索引与扫描行数，再结合数据分布、
回表成本和排序临时表判断瓶颈。不能只凭 SQL 文本断言某个索引一定生效。
"""

_CASE = Case(
    id="case17",
    kind="react",
    expected_events=[],
    user_messages=[
        "我想更深入地学习 MySQL，尤其是面试高频考点。"
        "请只在 javaguide.cn 搜索 5 条高质量资料供我选择。",
        f"我选择 {_GOOD_URL} 。请深读、审批并入库。",
        f"再测试这个低质量页面是否会被安全拒绝：{_BAD_URL}",
    ],
    cassette=_LLM_FIXTURE.name,
    react_fixture="web_acquisition",
)


class _SyntheticFetchSource:
    async def fetch(self, url: str, *, max_bytes: int) -> FetchedDocument:
        if url == _BAD_URL:
            raise FetchError("login_page", "网页正文质量门拒绝：login_page（正文字符数=0）")
        if url != _GOOD_URL:
            raise AssertionError(f"模型选择了未授权的录制 URL：{url}")
        encoded = _CONTENT.encode("utf-8")
        if len(encoded) > max_bytes:
            raise FetchError("too_large", f"合成正文超过录制上限：{len(encoded)} > {max_bytes}")
        return FetchedDocument(
            requested_url=url,
            final_url=url,
            canonical_url=url,
            title="MySQL 面试高频考点（合成 Eval 材料）",
            content=_CONTENT,
            content_type="text/html",
            content_hash=hashlib.sha256(encoded).hexdigest(),
            adapter="native_http",
            extractor="trafilatura:2.1.0",
            quality=DocumentQuality(content_char_count=len(_CONTENT)),
            trusted=False,
        )


async def main() -> None:
    endpoint = os.environ.get("SEARXNG_URL", "").strip()
    if not endpoint:
        raise RuntimeError("需要设置 SEARXNG_URL")

    provider = OpenAICompatProvider.from_env()
    llm_cassette = Cassette()
    acquisition_cassette = AcquisitionCassette()
    recording_provider = RecordingProvider(provider, llm_cassette, provider.model_for_role)
    recording_search = RecordingSearchProvider(
        SearXNGSearchProvider(endpoint=endpoint),
        acquisition_cassette,
        adapter_fingerprint=CASE17_SEARCH_FINGERPRINT,
    )
    recording_fetch = RecordingFetchSource(
        _SyntheticFetchSource(),
        acquisition_cassette,
        adapter_fingerprint=CASE17_FETCH_FINGERPRINT,
        normalization_version=CASE17_FETCH_NORMALIZATION,
    )
    try:
        result = await solve(
            _CASE,
            provider_override=recording_provider,
            search_provider_override=recording_search,
            fetch_source_override=recording_fetch,
        )
    finally:
        await provider.aclose()

    failures = grade_case17(result)
    if failures:
        print(f"case17 规则门失败：{failures}")
        print("工具调用：")
        for event in result.events:
            if event.type == "tool_call.started":
                print(f"  - {event.payload.get('tool_name')}({event.payload.get('arguments')})")
        print("失败事件：")
        for event in result.events:
            if event.type in {"learning.resource_fetch_failed", "learning.citation_rejected"}:
                print(f"  - {event.type}: {event.payload}")
        raise RuntimeError(f"case17 规则门未通过，拒绝保存 cassette：{failures}")

    _LLM_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    llm_cassette.save(_LLM_FIXTURE)
    acquisition_cassette.save(_ACQUISITION_FIXTURE)
    print(f"LLM cassette 已存：{_LLM_FIXTURE}")
    print(f"Acquisition cassette 已存：{_ACQUISITION_FIXTURE}")
    print("事件类型序列：")
    for event in result.events:
        print(f"  - {event.type}")
    print("工具调用：")
    for event in result.events:
        if event.type == "tool_call.started":
            print(f"  - {event.payload.get('tool_name')}({event.payload.get('arguments')})")


if __name__ == "__main__":
    asyncio.run(main())
