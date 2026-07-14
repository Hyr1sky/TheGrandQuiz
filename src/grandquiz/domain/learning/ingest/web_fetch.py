"""真实网络抓取源——httpx + SSRF 防护 + 逐跳重定向重验证 + HTML→纯文本提取。

补的是 ``fetch.py`` 模块文档里明确留的缝："真实 httpx / 超时 / DNS 是真 I/O 关注点，留给后续
human 以'实现一个真实的 source'的方式补齐"。``create_http_source`` 就是那个 source：注入进
``fetch_resource(source=...)``，行为契约不变（抛任何异常即抓取失败，被 ``fetch_resource``
归一成 ``FetchError``）。

**SSRF 防护是本模块的核心职责**（``fetch.py`` 的域名白名单管"允不允许抓"，这里管"抓的时候会不会
被骗去打内网"）：每次实际发出请求前，先把目标主机名解析成 IP、断言全部解析结果都是**全局可达**
地址（``ipaddress.ip_address(...).is_global`` 一次性排除私有 / 环回 / 链路本地 / 保留 / 组播——
比逐项枚举 ``is_private``/``is_loopback``/… 更不容易漏判）。**重定向逐跳重验证**：不用
``follow_redirects=True`` 一把梭，而是手动跟 ``response.next_request``、每一跳都重新做上面
的主机名解析检查——否则一个公网 URL 302 到内网地址（经典 SSRF-via-redirect 手法）会绕过检查。

**刻意不用第三方 SSRF 防护库**（如 httpx-secure）：核心检查逻辑就是"解析 IP + `is_global`"
一行代码，用标准库 ``ipaddress``/``socket`` 手写足够清楚、无需为此再添一个使用者寥寥的依赖
——安全相关代码这里选择"看得懂在查什么"而非"引入一个黑盒库"。已知的残余风险：本实现按
"先解析 DNS 检查、再让 httpx 自己连接"两步走，理论上存在 DNS 重绑定的 TOCTOU 窗口（检查和
真连接用的是两次独立解析）；对单用户个人工具场景（防的是"网页内容诱导 LLM 去探内网"，不是
"防御专业攻击者赛 DNS 时间窗口"）这个残余风险可接受，做成文档明确的已知局限而非假装没有。

**HTML→纯文本提取用标准库** ``html.parser.HTMLParser``（不引 BeautifulSoup 等第三方库）：
下游的 Reader 是 LLM 语义抽取，本就对"提取得不够精致"的文本有鲁棒性,只需要把 ``<script>``/
``<style>`` 之类噪声去掉、留下可读文本，不必语义级还原 markdown 排版。
"""

import ipaddress
import socket
from collections.abc import Callable
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

# 逐跳重定向重验证的最大跳数——超过即视为异常（大概率是重定向循环或恶意构造）。
_MAX_REDIRECTS = 5

# 只接受文本类内容；二进制 / 其它类型一律拒绝（不喂给 Reader 无意义的字节垃圾）。
_ALLOWED_CONTENT_TYPES = frozenset(
    {"text/html", "text/plain", "text/markdown", "application/xhtml+xml"}
)

# 供 SSRF 防护跳过的标签：脚本 / 样式 / 不可见模板内容，混进文本毫无价值且可能带噪声。
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template"})


def _assert_globally_reachable(hostname: str) -> None:
    """SSRF 防护核心：把 ``hostname`` 解析成 IP，断言每个解析结果都全局可达。

    ``getaddrinfo`` 可能返回多条（IPv4 + IPv6，或多个 A 记录）——**全部**都必须全局可达，
    有一条落在私有 / 环回 / 链路本地 / 保留 / 组播范围就拒绝（宁可错杀一个合法的多宿主主机，
    不放过一个能探内网的入口）。DNS 解析失败本身也视为拒绝（不给"解析不出来就放行"的漏洞）。
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"DNS 解析失败（SSRF 防护拒绝，宁可错杀）：{hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError(f"目标解析到非全局可达地址（SSRF 防护拒绝）：{hostname} → {ip}")


def _follow_validated_redirects(client: httpx.Client, request: httpx.Request) -> httpx.Response:
    """手动逐跳跟随重定向，每一跳发请求前都重新校验主机名（防 redirect-based SSRF 绕过）。

    ``client`` 不开 ``follow_redirects``（默认即关闭）：每次 ``client.send`` 若命中重定向，
    响应体不会被消费，``response.next_request`` 携下一跳请求——由本函数决定要不要继续跟，
    而非交给 httpx 自动跟（那样中间跳的主机名就从未被本模块看到过、无从校验）。
    """
    for _ in range(_MAX_REDIRECTS + 1):
        parsed = urlparse(str(request.url))
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"仅支持 http(s) URL：{request.url}")
        if parsed.hostname is None:
            raise ValueError(f"URL 缺主机名：{request.url}")
        _assert_globally_reachable(parsed.hostname)
        response = client.send(request)
        if response.next_request is None:
            return response
        request = response.next_request
    raise ValueError(f"重定向次数过多（超过 {_MAX_REDIRECTS} 跳）")


class _TextExtractor(HTMLParser):
    """把 HTML 折成纯文本：跳过 ``_SKIP_TAGS`` 标签内的内容，其余文本节点原样收集。"""

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.chunks.append(data)


def extract_text_from_html(html: str) -> str:
    """HTML → 纯文本：去脚本/样式噪声，折叠空白行（网页排版产生大量无意义缩进/空行）。"""
    parser = _TextExtractor()
    parser.feed(html)
    lines = [line.strip() for line in "".join(parser.chunks).splitlines()]
    return "\n".join(line for line in lines if line)


def create_http_source(
    *, timeout_seconds: float = 10.0, transport: httpx.BaseTransport | None = None
) -> Callable[[str], str]:
    """建一个真实抓取网页的 ``source``（供 ``fetch_resource(source=...)`` 注入）。

    ``transport``：可选注入点（供测试用 ``httpx.MockTransport`` 替身，不触真网络；生产不传，
    走 httpx 默认真实传输）——同 ``Clock``/``Provider`` 的确定性注入思路，只是这里注入的是
    "网络"这个非确定性边界本身，注定不可回放（真实网页内容会变），故本模块不追求可回放，
    只追求"抓取行为本身确定、安全"。
    """

    def source(url: str) -> str:
        client = httpx.Client(timeout=timeout_seconds, transport=transport)
        try:
            request = client.build_request("GET", url)
            response = _follow_validated_redirects(client, request)
            response.raise_for_status()
        finally:
            client.close()
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type not in _ALLOWED_CONTENT_TYPES:
            raise ValueError(f"不支持的内容类型：{content_type or '(未知)'}")
        if content_type in ("text/html", "application/xhtml+xml"):
            return extract_text_from_html(response.text)
        return response.text

    return source
