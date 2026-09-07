"""x1080x 来源模块（Discuz archiver 模式，游客可访问，无需论坛账号）。"""
import re
from pathlib import Path
from typing import Any, Mapping, Optional

from scrapers.core.contracts import (
    CrawlContext,
    CrawlRecord,
    CrawlTarget,
    DiscoveryResult,
)
from scrapers.core.cf_challenge import is_rate_limited
from scrapers.core.http import CrawlerHttpClient
from scrapers.core.models import FetchResult
from util.log_util import log

from .parser import X1080XParser

DEFAULT_BASE_URL = "https://agaghhh.cc"
DEFAULT_FID = 244
# typeid -> 分类名。站点分类调整时在配置里覆盖 typeids 即可。
DEFAULT_TYPE_MAP = {
    "5212": "国内成人",
    "5206": "亚洲有码",
    "5207": "亚洲无码",
    "5208": "FC2",
    "5216": "MGS",
    "5479": "中文字幕",
    "5213": "探花精选",
    "5217": "主播精选",
}


class X1080XSource:
    name = "x1080x"

    def __init__(self, config: Mapping[str, Any], parser: Optional[X1080XParser] = None):
        self.config = dict(config)
        self.base_url = str(
            self.config.get("base_url", DEFAULT_BASE_URL)
        ).rstrip("/")
        self.fid = int(self.config.get("fid", DEFAULT_FID))
        configured_typeids = self.config.get("typeids")
        self.type_map = {
            str(key): str(value)
            for key, value in (configured_typeids or DEFAULT_TYPE_MAP).items()
        }
        self.page_limit = max(1, int(self.config.get("page_limit", 3)))
        self.parser = parser or X1080XParser(self.type_map)

    def list_url(self, typeid: str, page: int) -> str:
        return (
            f"{self.base_url}/forum.php?mod=forumdisplay&fid={self.fid}"
            f"&archiver=1&page={page}&filter=typeid&typeid={typeid}"
        )

    def detail_url(self, tid: int) -> str:
        return f"{self.base_url}/forum.php?mod=viewthread&tid={tid}&archiver=1"

    def dump_empty_list_page(self, typeid: str, page: int, body) -> None:
        """首个列表页就解析出 0 条时留现场：告警并把响应体落盘。

        正常「到底」只会发生在深分页；首页为空大概率是页面结构变化、
        镜像域名跳转页或过盾返回了非目标内容，靠 dump 文件排查。
        """
        raw = body or b""
        if isinstance(raw, str):
            raw = raw.encode("utf-8", "ignore")
        match = re.search(rb"<title[^>]*>(.*?)</title>", raw[:5000], re.I | re.S)
        title = match.group(1).decode("utf-8", "ignore").strip() if match else ""
        dump_note = ""
        try:
            debug_dir = Path(__file__).resolve().parents[3] / "data" / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            dump_path = debug_dir / f"x1080x_list_{typeid}_p{page}.html"
            dump_path.write_bytes(raw)
            dump_note = f" dump={dump_path}"
        except OSError as exc:
            dump_note = f" dump_failed={exc}"
        log.warning(
            "x1080x 列表页解析为 0 条（疑似结构变化或非目标页面）: "
            f"typeid={typeid} page={page} bytes={len(raw)} "
            f"title={title!r}{dump_note}"
        )

    def discover(
        self,
        context: CrawlContext,
        http: CrawlerHttpClient,
    ) -> DiscoveryResult:
        targets = []
        seen_tids = set()
        list_failures = 0
        list_retries = 0
        pages_fetched = 0

        for typeid, section in self.type_map.items():
            for page in range(1, self.page_limit + 1):
                result = http.fetch(self.list_url(typeid, page), stage="list")
                list_retries += max(0, result.attempts - 1)
                if not result.ok:
                    list_failures += 1
                    log.warning(
                        "x1080x 列表页获取失败: "
                        f"typeid={typeid} page={page} "
                        f"error_type={result.error_type}"
                    )
                    break

                if is_rate_limited(result.body):
                    # 站点限流：立即中止整轮，继续请求只会加剧限流；
                    # 下一个调度周期自然重试。
                    raise RuntimeError(
                        f"x1080x 命中站点限流（请求过于频繁），中止本轮: "
                        f"typeid={typeid} page={page}"
                    )

                pages_fetched += 1
                tids = self.parser.parse_list(result.body)
                if not tids and page == 1:
                    # 首页 0 条多为过盾返回了中间态页面（byparr 并发时会发生），
                    # 重试一次再定论；仍为空则留诊断现场。
                    log.warning(
                        f"x1080x 分类 {typeid} 首页解析为 0 条，重试一次"
                    )
                    retry_fetch = http.fetch(
                        self.list_url(typeid, page), stage="list"
                    )
                    if retry_fetch.ok:
                        result = retry_fetch
                        tids = self.parser.parse_list(result.body)
                    if not tids:
                        self.dump_empty_list_page(typeid, page, result.body)
                if not tids:
                    # 空页视为该分类到底，不再翻后续页。
                    break
                for tid in tids:
                    if tid in seen_tids:
                        continue
                    seen_tids.add(tid)
                    targets.append(
                        CrawlTarget(
                            key=str(tid),
                            url=self.detail_url(tid),
                            partition=typeid,
                            metadata={
                                "tid": tid,
                                "typeid": typeid,
                                "section": section,
                            },
                        )
                    )

        if not pages_fetched:
            raise RuntimeError("x1080x 所有分类列表页均获取失败")

        return DiscoveryResult(
            targets=targets,
            failed=list_failures,
            details={
                "pages": pages_fetched,
                "list_pages_failed": list_failures,
                "list_retries": list_retries,
                "discovery_retries": list_retries,
            },
        )

    def parse_detail(
        self,
        target: CrawlTarget,
        result: FetchResult,
    ) -> Optional[CrawlRecord]:
        metadata = dict(target.metadata or {})
        tid = int(metadata.get("tid") or target.key)
        payload = self.parser.parse_detail(
            result.body,
            target.url,
            tid=tid,
            fid=self.fid,
            typeid=str(metadata.get("typeid") or ""),
            section=str(metadata.get("section") or ""),
        )
        if payload is None:
            return None
        if not payload.get("magnet"):
            # 无磁链帖对下游无价值；进失败台账按退避重试（磁链可能后补）。
            return None
        return CrawlRecord(target=target, payload=payload)
