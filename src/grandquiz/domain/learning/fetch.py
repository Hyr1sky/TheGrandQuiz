"""确定性、网络无关的抓取 + 守卫——抓取源经注入，本模块不碰真实网络。

抓回内容一律视为**不可信输入**（注入防护）：调用方据此把 ``LearningResource.trusted``
置为 False。本模块只做 domain 层能确定化的两道守卫——**域名白名单** + **大小上限**；
真实 httpx / 超时线程 / DNS 是真 I/O 关注点，本任务不做（见 issue 03 边界），留给后续
human 以"实现一个真实的 ``source``"的方式补齐，而不改本函数的守卫逻辑。
"""

import hashlib
from collections.abc import Callable, Collection
from urllib.parse import urlparse

from grandquiz.kernel.recovery import ErrorClass


class FetchError(Exception):
    """抓取失败的归一异常——域名不在白名单 / 超大小上限 / 注入源抛异常都收敛成它。

    调用方据此把资源标记 ``failed``、发 ``RESOURCE_FETCH_FAILED``，不产生幽灵 item
    （eval case 7）。深读链路的"部分失败"应经它优雅降级，而非炸掉整条 ingest。
    ``error_class = RESOURCE_UNREADABLE`` 供 kernel ``RecoveryPolicy`` / 事件归因（单资源不可读）。
    """

    error_class = ErrorClass.RESOURCE_UNREADABLE


def fetch_resource(
    url: str,
    *,
    source: Callable[[str], str],
    max_bytes: int,
    allowed_domains: Collection[str],
) -> tuple[str, str]:
    """经注入的 ``source`` 抓取 ``url``，返回 ``(content, content_hash)``。

    ``source``：注入的资源源（无真实网络）；给它一个 url、返回内容字符串，**抛任何异常
    即模拟抓取失败**。测试注入确定性 mock；生产由后续 human 实现真实 httpx 版本。

    守卫（失败一律 ``raise FetchError``，让 ingest 走失败分支而非崩溃）：

    - ``url`` 的域名不在 ``allowed_domains`` → 拒绝（域名白名单，注入防护，且不触发无谓抓取）；
    - ``source`` 抛异常 → 包成 ``FetchError``（抓取失败归一）；
    - ``source`` 返回内容的 UTF-8 字节数 > ``max_bytes`` → 拒绝（大小上限，防超大不可信输入）。

    ``content_hash = sha256(content 的 utf-8 字节).hexdigest()``——供资源持久化原始内容后
    校验 / 去重，日后回填出处无需重抓（真实 URL 会腐烂）。成功内容仍是不可信数据。
    """
    host = urlparse(url).hostname
    if host is None or host not in allowed_domains:
        raise FetchError(f"域名不在白名单：{host!r}（url={url}）")
    try:
        content = source(url)
    except FetchError:
        raise
    except Exception as exc:  # 注入源的任何失败都归一成 FetchError（部分失败不炸整条流）
        raise FetchError(f"抓取源失败：{exc!r}（url={url}）") from exc
    encoded = content.encode("utf-8")
    if len(encoded) > max_bytes:
        raise FetchError(f"内容超过大小上限：{len(encoded)} > {max_bytes} 字节（url={url}）")
    content_hash = hashlib.sha256(encoded).hexdigest()
    return content, content_hash
