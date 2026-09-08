"""x1080x 来源模块（Discuz archiver 模式，游客可访问，无需论坛账号）。"""
import re
from pathlib import Path
from typing import Any, Mapping, Optional

from scrapers.core.contracts import (
    CrawlContext,
    CrawlRecord,
    CrawlTarget,
    DiscoveryResult,
    DetailValidationError,
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

    def __init__(self, config: Mapping[str, Any], parser: Optional[X1080XParser] = None,
                 *, diagnostics: bool = True):
        self.config = dict(config)
        self.diagnostics = diagnostics
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
            if not self.diagnostics:
                return
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
                self._check_rate_limit(result)
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
                if not tids and page == 1:
                    # 首页 0 条多为过盾返回了中间态页面（byparr 并发时会发生），
                    # 重试一次再定论；仍为空则留诊断现场。
                    log.warning(
                        f"x1080x 分类 {typeid} 首页解析为 0 条，重试一次"
                    )
                    retry_fetch = http.fetch(
                        self.list_url(typeid, page), stage="list"
                    )
                    self._check_rate_limit(retry_fetch)
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
        try:
            payload = self.parser.parse_detail(
                result.body,
                target.url,
                tid=tid,
                fid=self.fid,
                typeid=str(metadata.get("typeid") or ""),
                section=str(metadata.get("section") or ""),
                strict=True,
            )
            if not payload.get("magnet"):
                raise DetailValidationError("missing_magnet")
        except DetailValidationError as exc:
            self._diagnose_detail(tid, result, exc.reason)
            raise
        return CrawlRecord(target=target, payload=payload)

    @staticmethod
    def _check_rate_limit(result):
        if result.error_type == "rate_limited" or is_rate_limited(result.body):
            raise RuntimeError("x1080x 命中站点限流，中止本轮增量抓取，等待下次调度")

    def _diagnose_detail(self, tid, result, reason):
        raw = result.body or b""
        match = re.search(rb"<title[^>]*>(.*?)</title>", raw[:5000], re.I | re.S)
        title = match.group(1).decode("utf-8", "replace")[:160] if match else ""
        dump_note = ""
        if self.diagnostics:
            try:
                debug_dir = Path(__file__).resolve().parents[3] / "data" / "debug"
                debug_dir.mkdir(parents=True, exist_ok=True)
                dump_path = debug_dir / f"x1080x_detail_{tid}.html"
                dump_path.write_bytes(raw[:512 * 1024])
                files = sorted(debug_dir.glob("x1080x_detail_*.html"),
                               key=lambda path: path.stat().st_mtime, reverse=True)
                for old in files[20:]:
                    old.unlink()
                dump_note = f" dump={dump_path}"
            except OSError as exc:
                dump_note = f" dump_failed={exc}"
        log.warning(f"x1080x 详情校验失败: tid={tid} reason={reason} "
                    f"status={result.status_code} bytes={len(raw)} title={title!r}{dump_note}")
