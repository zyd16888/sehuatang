"""
HTTP 抓取客户端（curl_cffi + chrome110 指纹模拟）

- 自动 R18 bypass：从「不详」拦截页提取 safeid 塞 cookie 重试
- 可选 CF bypass：配置 flaresolverr_url 后触发 CF 自动调用 FlareSolverr 过盾
- Cookie 加锁，模块级单例可被 ThreadPoolExecutor 多线程共享
"""
import os
import re
import threading
from dataclasses import replace
from typing import Optional

from curl_cffi import requests

from scrapers.core.cf_challenge import (
    CF_STATUS,
    FlareSolverrClient,
    is_cf_challenge,
    merge_solution_cookies,
)
from scrapers.core.config import HttpSettings, load_source_settings
from scrapers.core.http import CrawlerHttpClient
from util.log_util import log
from util.read_config import get_config

_SAFEID_RE = re.compile(r"safeid\s*=\s*['\"]([^'\"]+)['\"]")

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


class HttpClient:
    """sehuatang 专用 HTTP 客户端"""

    def __init__(
        self,
        settings: Optional[HttpSettings] = None,
        transport: Optional[CrawlerHttpClient] = None,
    ):
        full_config = get_config()
        self.settings = settings or load_source_settings(full_config, "sehuatang")
        self.headers = {"User-Agent": self.settings.user_agent or _DEFAULT_UA}
        self.cookie: dict = {"_safe": ""}
        self._cookie_lock = threading.Lock()
        self._timeout = self.settings.timeout
        source_config = (
            ((full_config.get("crawler") or {}).get("sources") or {}).get(
                "sehuatang"
            )
            or {}
        )
        challenge_config = source_config.get("challenge") or {}
        flaresolverr_url = str(
            os.getenv("CRAWLER_SEHUATANG_FLARESOLVERR_URL")
            or challenge_config.get("flaresolverr_url")
            or get_config("http_client.flaresolverr_url")
            or ""
        ).strip()
        self._flaresolverr = (
            FlareSolverrClient(
                flaresolverr_url,
                proxy_url=(
                    self.settings.proxy.url
                    if self.settings.proxy.enabled
                    else None
                ),
            )
            if flaresolverr_url
            else None
        )

        # 站点挑战状态由本类处理，不能先在通用层按普通 429/503 重试。
        retry = replace(
            self.settings.retry,
            statuses=tuple(
                status
                for status in self.settings.retry.statuses
                if status not in CF_STATUS
            ),
        )
        transport_settings = replace(self.settings, retry=retry)
        self._transport = transport or CrawlerHttpClient(
            "sehuatang",
            transport_settings,
            request_func=self._request_with_cookies,
        )

    # ---------- 公共 API ----------

    def get_html(self, url: str) -> Optional[bytes]:
        """获取页面 HTML（自动处理 R18 / CF）。失败返回 None。"""
        try:
            status, body = self._request(url)
        except Exception as e:
            log.error(f"请求失败: {url}, {e}")
            return None

        if self._is_cf_challenge(body, status):
            log.warning(f"触发 CF: {url}")
            return self._bypass_cf(url)

        if self._is_r18_block(body):
            log.info(f"触发 R18，提取 safeid 重试: {url}")
            if not self._update_safeid_from_body(body):
                log.warning(f"R18 页面未找到 safeid: {url}")
                return None
            try:
                status, body = self._request(url)
            except Exception as e:
                log.error(f"R18 重试失败: {url}, {e}")
                return None
            if self._is_r18_block(body):
                log.warning(f"R18 重试后仍被拦截: {url}")
                return None

        if status != 200 or not body:
            log.warning(f"请求异常 status={status} url={url}")
            return None
        return body

    # ---------- 内部 ----------

    def _request(self, url: str) -> tuple[int, bytes]:
        result = self._transport.fetch(url)
        return result.status_code or 0, (result.body or b"")

    def _request_with_cookies(self, url: str, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.update(self.headers)
        return requests.get(
            url,
            cookies=self._cookie_copy(),
            headers=headers,
            **kwargs,
        )

    def _cookie_copy(self) -> dict:
        with self._cookie_lock:
            return {k: v for k, v in self.cookie.items() if v}

    def _set_cookie(self, key: str, value: str) -> None:
        if not key:
            return
        with self._cookie_lock:
            self.cookie[key] = value

    def _update_cookies_from_solution(self, items) -> None:
        if not items:
            return
        with self._cookie_lock:
            merge_solution_cookies(self.cookie, items)

    @staticmethod
    def _is_cf_challenge(body: bytes, status: int) -> bool:
        return is_cf_challenge(body, status)

    @staticmethod
    def _is_r18_block(body: bytes) -> bool:
        # R18 拦截页稳定特征：体积极小（一般 2-3KB）+ 内嵌 `var safeid='xxx'` 脚本
        # 注：拦截页的 <title> 是随机名人名（如"不详""塞缪尔·约翰逊"），不可作判定
        if not body or len(body) > 10000:
            return False
        return b"var safeid" in body

    def _update_safeid_from_body(self, body: bytes) -> bool:
        try:
            m = _SAFEID_RE.search(body.decode("utf-8", errors="ignore"))
        except Exception:
            return False
        if not m:
            return False
        self._set_cookie("_safe", m.group(1))
        return True

    def _bypass_cf(self, url: str, max_retry: int = 3) -> Optional[bytes]:
        if self._flaresolverr is None:
            log.warning("未配置 flaresolverr_url，无法自动过 CF，请求放弃")
            return None
        solution = self._flaresolverr.solve(url, cookies=self._cookie_copy())
        if solution is None:
            return None
        body, cookies, user_agent = solution
        self._update_cookies_from_solution(cookies)
        if user_agent:
            self.headers["User-Agent"] = user_agent

        if self._is_r18_block(body) and max_retry > 0:
            if self._update_safeid_from_body(body):
                return self._bypass_cf(url, max_retry - 1)
        return body


http_client = HttpClient()
