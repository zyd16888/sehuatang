"""x1080x 来源模块（Discuz archiver 模式，游客可访问，无需论坛账号）。"""
from typing import Any, Mapping, Optional

from scrapers.core.contracts import (
    CrawlContext,
    CrawlRecord,
    CrawlTarget,
    DiscoveryResult,
)
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

                pages_fetched += 1
                tids = self.parser.parse_list(result.body)
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
