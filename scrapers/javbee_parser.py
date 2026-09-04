import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from util.javbee_code import normalize_code_key, resolve_javbee_code


class JavbeeParser:
    """解析 Javbee 列表页和详情页。"""

    def parse_list(self, html_content: bytes, base_url: str) -> List[str]:
        soup = BeautifulSoup(html_content, "html.parser")
        base_host = urlparse(base_url).netloc.lower()
        urls = []
        for anchor in soup.select("h5.title.is-4.is-spaced a[href]"):
            url = urljoin(base_url, anchor.get("href"))
            parsed = urlparse(url)
            if parsed.netloc.lower() != base_host or not parsed.path.startswith("/detail/"):
                continue
            urls.append(url)
        return list(dict.fromkeys(urls))

    def parse_last_page(self, html_content: bytes) -> int:
        soup = BeautifulSoup(html_content, "html.parser")
        pages = [1]
        for anchor in soup.select("a.pagination-link[href]"):
            match = re.search(r"[?&]page=(\d+)", anchor.get("href", ""))
            if match:
                pages.append(int(match.group(1)))
        return max(pages)

    def parse_detail(self, html_content: bytes, detail_url: str) -> Optional[Dict[str, Any]]:
        soup = BeautifulSoup(html_content, "html.parser")
        title_node = soup.select_one("h1.title.is-4.is-spaced a")
        date_node = soup.select_one('p.subtitle.is-6 a[href*="/date/"]')
        if not title_node or not date_node:
            return None

        date_value = self._extract_date(date_node.get("href", ""))
        if not date_value:
            return None

        image_node = soup.select_one("div.column img[data-src], div.column img[src]")
        image_url = ""
        if image_node:
            image_url = urljoin(
                detail_url,
                image_node.get("data-src") or image_node.get("src") or "",
            )

        size_node = soup.select_one("h1.title.is-4.is-spaced span")
        magnet_node = soup.select_one('a[href^="magnet:?"]')
        torrent_node = soup.select_one('a[href*="/download/torrent/"]')
        source_key = self.source_key_from_url(detail_url)
        title = title_node.get_text(strip=True)
        code_resolution = resolve_javbee_code(None, title)

        return {
            "source_key": source_key,
            "date": date_value,
            "url": detail_url,
            "title": title,
            "code": code_resolution.code,
            "code_normalized": normalize_code_key(code_resolution.code),
            "code_source": code_resolution.source,
            "code_confidence": code_resolution.confidence,
            "code_rule": code_resolution.rule,
            "title_kind": code_resolution.title_kind,
            "img": image_url,
            "size": size_node.get_text(strip=True) if size_node else "",
            "magnet": magnet_node.get("href") if magnet_node else None,
            "torrent": urljoin(detail_url, torrent_node.get("href")) if torrent_node else None,
        }

    @staticmethod
    def source_key_from_url(url: str) -> str:
        path = urlparse(url).path.rstrip("/")
        return path.rsplit("/", 1)[-1].lower()

    @staticmethod
    def _extract_date(href: str) -> Optional[str]:
        match = re.search(r"/date/([^/?#]+)", href)
        if not match:
            return None
        value = match.group(1).strip()
        for date_format in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(value, date_format).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None
