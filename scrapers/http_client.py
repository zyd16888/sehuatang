"""
HTTP 抓取客户端（curl_cffi + chrome110 指纹模拟）

- 自动 R18 bypass：从「不详」拦截页提取 safeid 塞 cookie 重试
- 可选 CF bypass：配置 flaresolverr_url 后触发 CF 自动调用 FlareSolverr 过盾
- Cookie 加锁，模块级单例可被 ThreadPoolExecutor 多线程共享
"""
import re
import threading
from typing import Optional

from curl_cffi import requests

from util.log_util import log
from util.read_config import get_config

_SAFEID_RE = re.compile(r"safeid\s*=\s*['\"]([^'\"]+)['\"]")
_TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)
_CF_TITLE_KEYWORDS = ("just a moment", "attention required")
_CF_STATUS = {403, 429, 503}

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


class HttpClient:
    """sehuatang 专用 HTTP 客户端"""

    def __init__(self):
        ua = get_config("browser.user_agent") or _DEFAULT_UA
        self.headers = {"User-Agent": ua}
        self.cookie: dict = {"_safe": ""}
        self._cookie_lock = threading.Lock()
        self._timeout = int(get_config("request_timeout", 15) or 15)
        self._flaresolverr_url = (get_config("flaresolverr_url") or "").strip() or None

        proxy_cfg = get_config("proxy") or {}
        if proxy_cfg.get("proxy_enable") and proxy_cfg.get("proxy_url"):
            p = proxy_cfg.get("proxy_url")
            self._proxies = {"http": p, "https": p}
        else:
            self._proxies = None

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
        r = requests.get(
            url,
            proxies=self._proxies,
            cookies=self._cookie_copy(),
            headers=self.headers,
            allow_redirects=True,
            timeout=self._timeout,
            impersonate="chrome110",
        )
        return r.status_code, (r.content or b"")

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
            for c in items:
                name = str(c.get("name") or "").strip()
                value = c.get("value")
                if name and value is not None:
                    self.cookie[name] = str(value)

    @staticmethod
    def _title(body: bytes) -> str:
        m = _TITLE_RE.search(body[:5000])
        if not m:
            return ""
        try:
            return m.group(1).decode("utf-8", errors="ignore").strip()
        except Exception:
            return ""

    @staticmethod
    def _is_cf_challenge(body: bytes, status: int) -> bool:
        if status in _CF_STATUS:
            return True
        title = HttpClient._title(body).lower()
        return any(k in title for k in _CF_TITLE_KEYWORDS)

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
        if not self._flaresolverr_url:
            log.warning("未配置 flaresolverr_url，无法自动过 CF，请求放弃")
            return None
        cookies = self._cookie_copy()
        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": 60000,
            "cookies": [{"name": k, "value": v} for k, v in cookies.items()],
        }
        if self._proxies and self._proxies.get("http"):
            payload["proxy"] = {"url": self._proxies["http"]}
        try:
            r = requests.post(
                self._flaresolverr_url,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=90,
            )
            solution = (r.json() or {}).get("solution") or {}
            if solution.get("status") != 200:
                log.error(f"FlareSolverr 返回异常 status={solution.get('status')} url={url}")
                return None

            self._update_cookies_from_solution(solution.get("cookies"))
            ua = solution.get("userAgent")
            if ua:
                self.headers["User-Agent"] = ua

            html = solution.get("response") or ""
            html = re.sub(
                r'charset=["\']?(gbk|gb2312|big5)["\']?',
                'charset="utf-8"',
                html,
                flags=re.I,
            )
            body = html.encode("utf-8")

            if self._is_r18_block(body) and max_retry > 0:
                if self._update_safeid_from_body(body):
                    return self._bypass_cf(url, max_retry - 1)
            return body
        except Exception as e:
            log.error(f"CF 过盾异常: {e}")
            return None


http_client = HttpClient()
