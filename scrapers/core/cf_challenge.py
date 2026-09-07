"""Cloudflare 挑战检测与 FlareSolverr 过盾客户端。

过盾成功后调用方应缓存 solution 里的 Cookie 和 User-Agent，
后续请求直连复用，仅在 Cookie 失效重新触发挑战时再次过盾。
"""
import re
from typing import Iterable, Mapping, Optional

from curl_cffi import requests

from util.log_util import log

CF_STATUS = (403, 429, 503)
_CF_TITLE_KEYWORDS = ("just a moment", "attention required")
_CF_BODY_MARKERS = (b"cf-challenge", b"__cf_chl", b"challenges.cloudflare.com")
_TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)
_CHARSET_META_RE = re.compile(
    r'charset=["\']?(gbk|gb2312|big5)["\']?',
    re.I,
)


def is_cf_challenge(body: Optional[bytes], status: Optional[int]) -> bool:
    if status in CF_STATUS:
        return True
    if not body:
        return False
    head = body[:5000]
    if any(marker in head for marker in _CF_BODY_MARKERS):
        return True
    match = _TITLE_RE.search(head)
    if not match:
        return False
    title = match.group(1).decode("utf-8", errors="ignore").strip().lower()
    return any(keyword in title for keyword in _CF_TITLE_KEYWORDS)


class FlareSolverrClient:
    """封装 FlareSolverr request.get，返回 (html_bytes, cookies, user_agent)。"""

    def __init__(
        self,
        endpoint: str,
        *,
        proxy_url: Optional[str] = None,
        max_timeout_ms: int = 60000,
        request_timeout: float = 90.0,
    ):
        self.endpoint = endpoint.strip().rstrip("/")
        self.proxy_url = proxy_url or None
        self.max_timeout_ms = int(max_timeout_ms)
        self.request_timeout = float(request_timeout)

    def solve(
        self,
        url: str,
        cookies: Optional[Mapping[str, str]] = None,
    ) -> Optional[tuple[bytes, list, Optional[str]]]:
        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": self.max_timeout_ms,
            "cookies": [
                {"name": name, "value": value}
                for name, value in (cookies or {}).items()
            ],
        }
        if self.proxy_url:
            payload["proxy"] = {"url": self.proxy_url}
        try:
            response = requests.post(
                self.endpoint,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=self.request_timeout,
            )
            solution = (response.json() or {}).get("solution") or {}
        except Exception as exc:
            log.error(f"FlareSolverr 请求失败: endpoint={self.endpoint} error={exc}")
            return None

        if solution.get("status") != 200:
            log.warning(
                "FlareSolverr 返回异常: "
                f"status={solution.get('status')} url={url}"
            )
            return None

        html = solution.get("response") or ""
        # lxml/bs4 会信任 meta charset；FlareSolverr 返回的已是 unicode 文本，
        # 统一改写为 utf-8 避免二次解码错乱。
        html = _CHARSET_META_RE.sub('charset="utf-8"', html)
        body = html.encode("utf-8")
        if is_cf_challenge(body, 200):
            log.warning(f"FlareSolverr 过盾后仍是挑战页: url={url}")
            return None
        return body, list(solution.get("cookies") or []), solution.get("userAgent")


def merge_solution_cookies(
    cookie_store: dict,
    solution_cookies: Iterable[Mapping],
) -> None:
    """把 FlareSolverr solution 的 Cookie 合并进调用方的缓存字典。"""
    for cookie in solution_cookies or []:
        name = str(cookie.get("name") or "").strip()
        value = cookie.get("value")
        if name and value is not None:
            cookie_store[name] = str(value)
