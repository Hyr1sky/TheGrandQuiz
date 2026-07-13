"""确定性、网络无关的抓取 + 守卫——抓取源经注入，本模块不碰真实网络。

抓回内容一律视为**不可信输入**（注入防护）：调用方据此把 ``LearningResource.trusted``
置为 False。本模块只做 domain 层能确定化的两道守卫——**域名白名单**（或显式放开的
``ALLOW_ANY_DOMAIN``）+ **大小上限**；真实 httpx / 超时 / DNS / SSRF 防护是真 I/O 关注点，
见 ``web_fetch.py`` 的 ``create_http_source``——本模块的域名白名单管的是"这个 url 允不允许
抓"（注入防护：LLM 不能凭空调用未授权域名），``web_fetch.py`` 管的是"抓的时候会不会被
骗去打内网"（SSRF），两层职责不同、不互相替代。个人工具"粘贴任意文章 URL 来学"的场景下，
预先登记域名不现实，故显式放开需要调用方主动传 ``ALLOW_ANY_DOMAIN``（而非默默改默认值）。
"""

import hashlib
from collections.abc import Callable, Collection
from typing import Literal
from urllib.parse import urlparse

from grandquiz.kernel.recovery import ErrorClass

# 显式放开域名白名单的哨兵值（而非默认全放行）：真实网络抓取场景下，用户会粘贴任意 URL，
# 预先登记允许域名不现实——但"放开"必须是调用方在装配点看得见的显式选择,不是隐式默认。
ALLOW_ANY_DOMAIN: Literal["*"] = "*"


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
    allowed_domains: Collection[str] | Literal["*"],
) -> tuple[str, str]:
    """经注入的 ``source`` 抓取 ``url``，返回 ``(content, content_hash)``。

    ``source``：注入的资源源；给它一个 url、返回内容字符串，**抛任何异常即模拟/视为抓取失败**。
    测试注入确定性 mock；生产可注入 ``web_fetch.create_http_source()`` 的真 httpx 实现。

    守卫（失败一律 ``raise FetchError``，让 ingest 走失败分支而非崩溃）：

    - ``url`` 无主机名，或（``allowed_domains`` 不是 ``ALLOW_ANY_DOMAIN`` 时）域名不在其中
      → 拒绝（域名白名单，注入防护，且不触发无谓抓取）；
    - ``source`` 抛异常 → 包成 ``FetchError``（抓取失败归一）；
    - ``source`` 返回内容的 UTF-8 字节数 > ``max_bytes`` → 拒绝（大小上限，防超大不可信输入）。

    ``content_hash = sha256(content 的 utf-8 字节).hexdigest()``——供资源持久化原始内容后
    校验 / 去重，日后回填出处无需重抓（真实 URL 会腐烂）。成功内容仍是不可信数据。
    """
    host = urlparse(url).hostname
    if host is None:
        raise FetchError(f"URL 缺主机名（url={url}）")
    if allowed_domains != ALLOW_ANY_DOMAIN and host not in allowed_domains:
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
