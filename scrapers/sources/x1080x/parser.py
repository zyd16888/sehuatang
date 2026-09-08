"""解析 x1080x（Discuz archiver 模式）列表页和详情页。

选择器同时覆盖 archiver 简化页和普通页两套 DOM，
以兼容站点在不同镜像/版本间的差异。
"""
import re
from html import unescape
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
from scrapers.core.contracts import DetailValidationError

from util.javbee_code import normalize_code_key, resolve_x1080x_code

_TID_QUERY_RE = re.compile(r"(?:^|[?&])tid=(\d+)")
_TID_PATH_RE = re.compile(r"(?:^|/)thread-(\d+)-")
_PUBLISH_DATE_RE = re.compile(
    r"(?:发表于|發表於)\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})"
)
_MAGNET_RE = re.compile(r"""magnet:\?[^\s<>"'\[]+""", re.I)
_CODE_BLOCK_RE = re.compile(r"\[code\](.*?)\[/code\]", re.S | re.I)
_IMG_BBCODE_RE = re.compile(r"\[img\](.*?)\[/img\]", re.I)
_UNAVAILABLE_MARKERS = (
    "database error",
    "archiver 功能没有开",
    "archiver 功能沒有開",
)
_GIF_MARKERS = (".gif", "format=gif", "ext=gif", "type=gif", "image/gif")


def _ordered_unique(values) -> list:
    return list(dict.fromkeys(values))


def _is_gif_url(url: str) -> bool:
    candidate = str(url or "").strip().lower()
    return bool(candidate) and any(marker in candidate for marker in _GIF_MARKERS)


class X1080XParser:
    """x1080x archiver 页面解析器。"""

    def __init__(self, type_map: Optional[Dict[str, str]] = None):
        self.type_map = dict(type_map or {})

    def is_unavailable(self, soup: BeautifulSoup, text: str) -> bool:
        title = (soup.title.get_text(strip=True) if soup.title else "").lower()
        body_text = text.lower()
        return any(
            marker in title or marker in body_text
            for marker in _UNAVAILABLE_MARKERS
        )

    def parse_list(self, html_content: bytes) -> List[int]:
        """从列表页提取帖子 tid（保持页面顺序、去重）。"""
        soup = BeautifulSoup(html_content, "html.parser")
        if self.is_unavailable(soup, soup.get_text(" ", strip=True)[:2000]):
            return []

        scope = (
            soup.select_one("#threadlist")
            or soup.select_one("#content")
            or soup
        )
        tids = []
        for anchor in scope.select('a[href*="viewthread"], a[href*="thread-"]'):
            href = anchor.get("href") or ""
            match = _TID_QUERY_RE.search(urlparse(href).query or href)
            if match:
                tids.append(int(match.group(1)))
                continue
            match = _TID_PATH_RE.search(href)
            if match:
                tids.append(int(match.group(1)))
        return _ordered_unique(tids)

    def parse_detail(
        self,
        html_content: bytes,
        detail_url: str,
        *,
        tid: int,
        fid: int,
        typeid: str = "",
        section: str = "",
        strict: bool = False,
    ) -> Optional[Dict[str, Any]]:
        def invalid(reason):
            if strict:
                raise DetailValidationError(reason)
            return None

        soup = BeautifulSoup(html_content, "html.parser")
        page_text = soup.get_text(" ", strip=True)
        if self.is_unavailable(soup, page_text[:2000]):
            return invalid("page_unavailable")

        title = self._extract_title(soup)
        if not title:
            return invalid("missing_title")

        date_value = self._extract_publish_date(soup)
        if not date_value:
            return invalid("missing_date")

        content_node = self._content_node(soup)
        if content_node is None:
            return invalid("missing_content")
        content_html = content_node.decode_contents()
        content_text = content_node.get_text("\n", strip=True)

        magnets = self._extract_magnets(content_html, content_text)
        page_typeid, page_section = self._extract_section(soup)
        resolved_typeid = page_typeid or str(typeid or "")
        resolved_section = (
            page_section
            or self.type_map.get(resolved_typeid, "")
            or str(section or "")
        )

        code_resolution = resolve_x1080x_code(title, magnets)
        return {
            "source_key": str(tid),
            "tid": int(tid),
            "fid": int(fid),
            "typeid": resolved_typeid,
            "section": resolved_section,
            "title": title,
            "code": code_resolution.code,
            "code_normalized": normalize_code_key(code_resolution.code),
            "code_source": code_resolution.source,
            "code_confidence": code_resolution.confidence,
            "date": date_value,
            "url": detail_url,
            "magnet": magnets[0] if magnets else None,
            "magnets": magnets,
            "img": self._extract_images(content_node, content_html, detail_url),
        }

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> str:
        node = soup.select_one("#thread_subject")
        if node:
            title = node.get_text(strip=True)
            if title:
                return title

        nav = soup.select_one("#nav") or soup.select_one("#pt")
        if nav:
            nav_text = nav.get_text(" ", strip=True)
            parts = [
                part.strip()
                for part in re.split(r"[›»>]+", nav_text)
                if part.strip()
            ]
            if parts:
                return parts[-1]
        return ""

    @staticmethod
    def _extract_publish_date(soup: BeautifulSoup) -> Optional[str]:
        candidates = []
        for node in soup.select('em[id^="authorposton"]'):
            candidates.append(node.get_text(" ", strip=True))
        for node in soup.select("p.author"):
            candidates.append(node.get_text(" ", strip=True))
        for text in candidates:
            match = _PUBLISH_DATE_RE.search(text)
            if match:
                raw = match.group(1).replace("/", "-").replace(".", "-")
                parts = raw.split("-")
                if len(parts) == 3:
                    return f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
        return None

    @staticmethod
    def _content_node(soup: BeautifulSoup):
        node = soup.select_one('[id^="postmessage_"]')
        if node is not None:
            return node
        return soup.select_one("#content")

    @staticmethod
    def _extract_magnets(content_html: str, content_text: str) -> List[str]:
        candidates = []
        decoded_html = unescape(content_html or "")
        decoded_text = unescape(content_text or "")

        for block in _CODE_BLOCK_RE.findall(decoded_html):
            block = block.strip()
            if block.lower().startswith("magnet:?"):
                candidates.append(block)

        for source in (decoded_html, decoded_text):
            candidates.extend(
                match.rstrip(".,;，。")
                for match in _MAGNET_RE.findall(source)
            )
        return _ordered_unique(
            value.strip()
            for value in candidates
            if value.strip().lower().startswith("magnet:?")
        )

    @staticmethod
    def _extract_images(content_node, content_html: str, base_url: str) -> List[str]:
        candidates = list(_IMG_BBCODE_RE.findall(content_html or ""))
        for image in content_node.select("img"):
            for attr in ("file", "zoomfile", "src"):
                value = (image.get(attr) or "").strip()
                if value:
                    candidates.append(value)

        urls = []
        for candidate in candidates:
            url = urljoin(base_url, candidate.strip())
            if urlparse(url).scheme in ("http", "https") and not _is_gif_url(url):
                urls.append(url)
        return _ordered_unique(urls)

    def _extract_section(self, soup: BeautifulSoup) -> tuple[str, str]:
        """优先从 typeid 链接取分类；其次从面包屑匹配已知分类名。"""
        for anchor in soup.select('a[href*="typeid="]'):
            href = anchor.get("href") or ""
            type_id = parse_qs(urlparse(href).query).get("typeid", [""])[0]
            if type_id in self.type_map:
                text = anchor.get_text(strip=True)
                section = (
                    text
                    if text in self.type_map.values()
                    else self.type_map[type_id]
                )
                return type_id, section

        nav = soup.select_one("#nav") or soup.select_one("#pt")
        if nav:
            parts = [
                part.strip()
                for part in re.split(r"[›»>]+", nav.get_text(" ", strip=True))
                if part.strip()
            ]
            known = {name: type_id for type_id, name in self.type_map.items()}
            for part in reversed(parts):
                if part in known:
                    return known[part], part
        return "", ""
