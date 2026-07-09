"""SqliteLearningStore 测试（M7）——与 dict 版 ``LearningStore`` 行为等价 + 模型往返保真。

行为等价：add/get、set_status、items_for_resource / items_for_task 的两跳聚合与稳定序，逐条比对
dict 版结果。往返保真：KnowledgeItem / Resource / Task 经 SQLite 存取后**逐字段一致**——含中文、
evidence 的 JSON 序列化（多条 + ``locator=None`` / 有值）、``raw_content=None``、``trusted`` 布尔、
``domain=None`` / ``concept_key=None``。用 ``:memory:`` db（单连接内足够；跨会话验收见
test_sqlite_persistence.py）。
"""

from pathlib import Path

from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource, LearningTask
from grandquiz.domain.learning.store import LearningStore, SqliteLearningStore, Store


def _item(resource_id: str, index: int, concept: str) -> KnowledgeItem:
    return KnowledgeItem.create(
        resource_id=resource_id,
        index=index,
        concept=concept,
        summary="摘要",
        evidence=[Evidence(quote="原文片段")],
        confidence=0.8,
    )


def _sqlite() -> SqliteLearningStore:
    return SqliteLearningStore(":memory:")


# --- 与 dict 版行为等价 ------------------------------------------------------------


def test_add_and_get_task() -> None:
    store = _sqlite()
    task = LearningTask.create("React")
    store.add_task(task)
    assert store.get_task(task.task_id) == task
    assert store.get_task("nope") is None


def test_add_task_is_idempotent() -> None:
    store = _sqlite()
    task = LearningTask.create("React")
    store.add_task(task)
    store.add_task(task)  # 幂等：INSERT OR REPLACE，不报错、不重复
    assert store.get_task(task.task_id) == task


def test_add_and_get_resource() -> None:
    store = _sqlite()
    resource = LearningResource.create(task_id="t", url="https://example.com/a")
    store.add_resource(resource)
    assert store.get_resource(resource.resource_id) == resource
    assert store.get_resource("nope") is None


def test_set_resource_status_updates_stored_resource() -> None:
    store = _sqlite()
    resource = LearningResource.create(task_id="t", url="https://example.com/a")
    store.add_resource(resource)
    store.set_resource_status(resource.resource_id, "failed")
    updated = store.get_resource(resource.resource_id)
    assert updated is not None
    assert updated.status == "failed"


def test_set_resource_status_missing_raises_keyerror() -> None:
    # 与 dict 版一致：资源不存在则报错（不静默建档）。
    store = _sqlite()
    try:
        store.set_resource_status("nope", "failed")
    except KeyError:
        pass
    else:  # pragma: no cover - 断言路径
        raise AssertionError("资源不存在时应抛 KeyError")


def test_items_for_resource_returns_only_that_resource_in_order() -> None:
    store = _sqlite()
    store.add_items([_item("r1", 0, "闭包"), _item("r1", 1, "提升"), _item("r2", 0, "无关")])
    got = store.items_for_resource("r1")
    assert [i.item_id for i in got] == ["r1#000", "r1#001"]
    assert [i.concept for i in got] == ["闭包", "提升"]


def test_items_for_task_aggregates_across_that_task_resources() -> None:
    store = _sqlite()
    ra = LearningResource.create(task_id="A", url="https://example.com/a")
    rb = LearningResource.create(task_id="A", url="https://example.com/b")
    rc = LearningResource.create(task_id="B", url="https://example.com/c")
    for r in (ra, rb, rc):
        store.add_resource(r)
    store.add_items([_item(ra.resource_id, 0, "x"), _item(rb.resource_id, 0, "y")])
    store.add_items([_item(rc.resource_id, 0, "z")])

    got = store.items_for_task("A")
    assert {i.concept for i in got} == {"x", "y"}
    assert [i.concept for i in store.items_for_task("B")] == ["z"]


def test_matches_dict_store_on_shared_scenario() -> None:
    # 同一操作序列喂 dict 版与 SQLite 版，读回结果逐条相等（行为等价的直接断言）。
    dict_store = LearningStore()
    sqlite_store = _sqlite()
    ra = LearningResource.create(task_id="A", url="https://example.com/a")
    items = [_item(ra.resource_id, i, c) for i, c in enumerate(["闭包", "提升", "作用域"])]
    for store in (dict_store, sqlite_store):
        store.add_resource(ra)
        store.add_items(items)
    # items_for_resource 逐字段一致（dict 版按写入序，SQLite 版按 item_id 序，此处两者一致）。
    assert sqlite_store.items_for_resource(ra.resource_id) == dict_store.items_for_resource(
        ra.resource_id
    )
    assert sqlite_store.items_for_task("A") == dict_store.items_for_task("A")


# --- 模型往返保真（含中文 / evidence JSON / None 列 / bool） -------------------------


def test_knowledge_item_round_trip_field_by_field() -> None:
    store = _sqlite()
    item = KnowledgeItem.create(
        resource_id="res123",
        index=7,
        concept="闭包捕获变量而非值",
        summary="闭包保存的是变量引用，循环里共享同一绑定",
        evidence=[
            Evidence(quote="闭包捕获的是变量而不是当时的值"),
            Evidence(quote="第 3 节示例", locator="section-3"),
        ],
        confidence=0.66,
    )
    store.add_items([item])
    got = store.items_for_resource("res123")
    assert len(got) == 1
    # 逐字段一致：item_id / 中文 concept & summary / evidence（含 locator=None 与有值）/ 置信度 /
    # concept_key=None。pydantic 值相等即逐字段相等。
    assert got[0] == item
    assert got[0].evidence[0].locator is None
    assert got[0].evidence[1].locator == "section-3"
    assert got[0].concept_key is None


def test_resource_round_trip_preserves_none_and_bool() -> None:
    store = _sqlite()
    # 深读前：raw_content / content_hash 为 None，trusted False，status pending。
    pending = LearningResource.create(task_id="t", url="https://example.com/x")
    store.add_resource(pending)
    assert store.get_resource(pending.resource_id) == pending
    # 深读后：回填内容 + hash、trusted 显式 True（验 bool 往返 0/1 不失真）、status read。
    read = pending.model_copy(
        update={
            "raw_content": "正文内容含中文",
            "content_hash": "abc123",
            "trusted": True,
            "status": "read",
        }
    )
    store.add_resource(read)
    got = store.get_resource(pending.resource_id)
    assert got == read
    assert got is not None and got.trusted is True


def test_task_round_trip_with_and_without_domain() -> None:
    store = _sqlite()
    plain = LearningTask.create("React")
    with_domain = LearningTask.create("线性代数", domain="理科")
    store.add_task(plain)
    store.add_task(with_domain)
    assert store.get_task(plain.task_id) == plain
    got = store.get_task(with_domain.task_id)
    assert got == with_domain
    assert got is not None and got.domain == "理科"


def test_task_round_trip_preserves_non_default_language() -> None:
    # language 往返保真：默认中文与显式非中文都须原样存取（mutation：不写 / 不读 language 列 →
    # English task 退回默认"中文" → 红）。
    store = _sqlite()
    chinese = LearningTask.create("React")  # 默认"中文"
    english = LearningTask.create("Algorithms", language="English")
    store.add_task(chinese)
    store.add_task(english)
    assert store.get_task(chinese.task_id) == chinese
    got = store.get_task(english.task_id)
    assert got == english
    assert got is not None and got.language == "English"


def test_matches_dict_store_on_task_language() -> None:
    # dict↔SQLite parity 覆盖 language：同一 task 喂两版，读回逐字段相等（含 language）。
    dict_store = LearningStore()
    sqlite_store = _sqlite()
    task = LearningTask.create("Algorithms", domain="CS", language="English")
    for store in (dict_store, sqlite_store):
        store.add_task(task)
    assert sqlite_store.get_task(task.task_id) == dict_store.get_task(task.task_id)
    got = sqlite_store.get_task(task.task_id)
    assert got is not None and got.language == "English"


def test_add_items_is_idempotent_overwrite() -> None:
    # INSERT OR REPLACE：同 item_id 二次入库覆盖、不重复成行（同 dict 版覆盖语义）。
    store = _sqlite()
    first = _item("r1", 0, "闭包")
    store.add_items([first])
    updated = KnowledgeItem.create(
        resource_id="r1",
        index=0,
        concept="闭包（修订）",
        summary="改后的摘要",
        evidence=[Evidence(quote="新的原文")],
        confidence=0.95,
    )
    store.add_items([updated])
    got = store.items_for_resource("r1")
    assert len(got) == 1
    assert got[0] == updated


def test_tmp_path_file_db_works(tmp_path: Path) -> None:
    # 真实文件 db（非 :memory:）也能正常读写——跨会话验收的地基。
    db = tmp_path / "learning.db"
    store = SqliteLearningStore(db)
    item = _item("r1", 0, "闭包")
    store.add_items([item])
    assert store.items_for_resource("r1") == [item]
    store.close()
    assert db.exists()


def test_multi_resource_order_matches_dict_and_is_item_id_sorted() -> None:
    # 跨实现顺序契约（M7 终审修复）：多资源任务下 dict 与 SQLite 的 items_for_task 顺序须一致
    # （都按 item_id 升序）——否则 select_target 的 rng.choice 跨实现会选中不同 item。
    task = LearningTask.create("React")
    # 两资源，resource_id 由 url 派生（哈希序 != 插入序），构造能暴露分歧的多资源场景。
    r_a = LearningResource.create(task_id=task.task_id, url="https://example.com/z")
    r_b = LearningResource.create(task_id=task.task_id, url="https://example.com/a")
    items = [
        _item(r_a.resource_id, 0, "闭包"),
        _item(r_b.resource_id, 0, "作用域"),
        _item(r_a.resource_id, 1, "提升"),
    ]
    stores: list[Store] = [LearningStore(), _sqlite()]
    for store in stores:
        store.add_task(task)
        store.add_resource(r_a)
        store.add_resource(r_b)
        store.add_items(items)

    expected = sorted(item.item_id for item in items)  # 按 item_id 升序的确定性顺序
    ids = [[i.item_id for i in store.items_for_task(task.task_id)] for store in stores]
    assert ids[0] == expected  # dict 版
    assert ids[1] == expected  # SQLite 版
    assert ids[0] == ids[1]  # 两实现顺序一致 → 选题跨实现不漂移


# --- all_items：全库全局读（不 join resources、不按 task 过滤，按 item_id 升序） --------


def test_all_items_empty_returns_empty() -> None:
    assert _sqlite().all_items() == []


def test_all_items_returns_whole_kb_item_id_sorted() -> None:
    # 跨 task 全库读：不按 task_id 过滤，全部 item 按 item_id 升序（修 #2 的读语义）。
    store = _sqlite()
    ra = LearningResource.create(task_id="A", url="https://example.com/a")
    rb = LearningResource.create(task_id="B", url="https://example.com/b")
    for r in (ra, rb):
        store.add_resource(r)
    store.add_items([_item(ra.resource_id, 0, "x"), _item(rb.resource_id, 0, "y")])
    got = store.all_items()
    assert {i.concept for i in got} == {"x", "y"}
    assert [i.item_id for i in got] == sorted(i.item_id for i in got)


def test_all_items_parity_dict_vs_sqlite_across_hash_prefixes() -> None:
    # 全局读 parity（选题 replay 命门）：多个不同 hash 前缀 resource_id 下的 item，dict 与
    # SQLite 的 all_items() 序列**逐条相等**（跨资源、稳定按 item_id 升序，不依赖插入序）。
    dict_store = LearningStore()
    sqlite_store = _sqlite()
    # 三个 url 派生出不同哈希前缀的 resource_id（哈希序 != 插入序），分挂不同 task。
    resources = [
        LearningResource.create(task_id="A", url="https://example.com/z"),
        LearningResource.create(task_id="B", url="https://example.com/a"),
        LearningResource.create(task_id="A", url="https://example.com/m"),
    ]
    items = [
        _item(r.resource_id, i, c)
        for i, (r, c) in enumerate(zip(resources, ["闭包", "作用域", "提升"], strict=True))
    ]
    for store in (dict_store, sqlite_store):
        for r in resources:
            store.add_resource(r)
        store.add_items(items)
    expected = sorted(items, key=lambda it: it.item_id)
    dict_items = dict_store.all_items()
    sqlite_items = sqlite_store.all_items()
    assert [i.item_id for i in dict_items] == [i.item_id for i in expected]  # 稳定升序
    assert dict_items == sqlite_items  # 两实现逐条相等 → 选题 rng.choice 跨实现不漂
