"""x1080x 抓取入口，使用公共爬虫引擎。"""
import os
from typing import Dict, Optional

from scrapers.core.config import load_source_settings
from scrapers.core.contracts import CrawlTarget
from scrapers.core.engine import CrawlEngine
from scrapers.core.http import CrawlerHttpClient
from scrapers.infrastructure import build_failure_store
from scrapers.page_backfill import FixedTargetSource, PageCheckpointStore
from scrapers.sources.x1080x import X1080XRepository, X1080XSource
from scrapers.sources.x1080x.http_client import X1080XHttpClient
from util.log_util import log
from util.mongo import find_existing_x1080x_keys, save_x1080x_items
from util.read_config import get_config


def _resolve_flaresolverr_url(config: dict) -> str:
    challenge = dict(config.get("challenge") or {})
    return str(
        os.getenv("CRAWLER_X1080X_FLARESOLVERR_URL")
        or challenge.get("flaresolverr_url")
        or ""
    ).strip()


class X1080XScraper:
    def __init__(self, config=None, http=None, failure_store=None):
        self.config = dict(config or get_config("x1080x", {}) or {})
        base_url = os.getenv("CRAWLER_X1080X_BASE_URL", "").strip()
        if base_url:
            self.config["base_url"] = base_url
        settings_config = (
            {"crawler": {"sources": {"x1080x": self.config}}}
            if "http" in self.config or "concurrency" in self.config
            else {"x1080x": self.config}
        )
        self.settings = load_source_settings(settings_config, "x1080x")
        self.http = http or X1080XHttpClient(
            self.settings,
            flaresolverr_url=_resolve_flaresolverr_url(self.config),
        )
        self.failure_store = failure_store or build_failure_store(
            mongodb_enabled=bool(get_config("mongodb.enable", False))
        )

    def crawl(
        self,
        *,
        dry_run: bool = False,
        retry_failed: bool = False,
    ) -> Dict[str, object]:
        source = X1080XSource(self.config)
        repository = X1080XRepository(
            existing_lookup=find_existing_x1080x_keys,
            save_func=save_x1080x_items,
            refresh_all=bool(self.config.get("refresh_all", False)),
        )
        summary = CrawlEngine(self.http, self.failure_store).run(
            source,
            repository,
            dry_run=dry_run,
            retry_failed=retry_failed,
        )
        summary.details["existing"] = repository.existing_count
        self._notify_new_items(repository, dry_run=dry_run, retry_failed=retry_failed)
        result = summary.as_dict()
        log.info(
            "x1080x 抓取汇总: "
            f"status={result['status']} pages={result.get('pages', 0)} "
            f"discovered={result['discovered']} existing={result['existing']} "
            f"requested={result['requested']} failed={result['failed']} "
            f"saved={result['saved']} updated={result['updated']}"
        )
        return result

    def _notify_new_items(
        self,
        repository: X1080XRepository,
        *,
        dry_run: bool,
        retry_failed: bool,
    ) -> None:
        """仅在定时/手动的增量抓取后推送新数据。

        dry-run 不落库、retry-failed 是失败恢复、refresh_all 会重发旧数据，
        这三种场景都不通知；backfill_pages 也不经过本方法。
        """
        if dry_run or retry_failed or repository.refresh_all:
            return
        if not repository.last_saved_payloads:
            return
        if not bool(self.config.get("notify_telegram", True)):
            return
        try:
            from scrapers.notification_manager import NotificationManager

            NotificationManager().send_x1080x_notifications(
                repository.last_saved_payloads
            )
        except Exception as exc:
            log.error(f"x1080x 通知发送失败（不影响抓取结果）: {exc}")

    def backfill_pages(
        self,
        start_page: int,
        end_page: int,
        *,
        typeids=None,
        resume: bool = False,
        dry_run: bool = False,
        checkpoint_store: Optional[PageCheckpointStore] = None,
    ) -> Dict[str, object]:
        """按页区间补抓历史数据。

        检查点按完整处理完的页推进；详情失败进失败台账、
        由 retry-failed 恢复，不阻塞页进度。
        """
        source = X1080XSource(self.config)
        repository = X1080XRepository(
            existing_lookup=find_existing_x1080x_keys,
            save_func=save_x1080x_items,
        )
        engine = CrawlEngine(self.http, self.failure_store)
        checkpoints = checkpoint_store or PageCheckpointStore()

        selected = {
            str(typeid): source.type_map[str(typeid)]
            for typeid in (typeids or source.type_map)
            if str(typeid) in source.type_map
        }
        if not selected:
            raise ValueError(f"未匹配到任何 typeid: {typeids}")

        summary = {
            "source": "x1080x",
            "start_page": start_page,
            "end_page": end_page,
            "pages_scanned": 0,
            "discovered": 0,
            "existing": 0,
            "requested": 0,
            "saved": 0,
            "failed": 0,
            "partitions": {},
        }
        seen_tids = set()

        for typeid, section in selected.items():
            first_page = start_page
            if resume:
                completed = checkpoints.load("x1080x", typeid)
                if completed:
                    first_page = max(start_page, completed + 1)
                    log.info(
                        f"x1080x 分类 {typeid} 从检查点第 {first_page} 页继续"
                    )
            partition_summary = {
                "section": section,
                "pages": 0,
                "saved": 0,
                "failed": 0,
                "stopped": "",
            }
            summary["partitions"][typeid] = partition_summary

            for page in range(first_page, end_page + 1):
                result = self.http.fetch(source.list_url(typeid, page), stage="list")
                if not result.ok:
                    partition_summary["stopped"] = f"list_failed@{page}"
                    summary["failed"] += 1
                    log.warning(
                        "x1080x 补抓列表页失败，该分类暂停（检查点未推进，可 --resume 继续）: "
                        f"typeid={typeid} page={page} error_type={result.error_type}"
                    )
                    break

                tids = source.parser.parse_list(result.body)
                if not tids and page == first_page:
                    # 与增量 discover 一致：首个页面 0 条先重试一次再定论。
                    log.warning(
                        f"x1080x 分类 {typeid} 第 {page} 页解析为 0 条，重试一次"
                    )
                    retry_fetch = self.http.fetch(
                        source.list_url(typeid, page), stage="list"
                    )
                    if retry_fetch.ok:
                        result = retry_fetch
                        tids = source.parser.parse_list(result.body)
                    if not tids:
                        source.dump_empty_list_page(typeid, page, result.body)
                if not tids:
                    partition_summary["stopped"] = f"exhausted@{page}"
                    log.info(f"x1080x 分类 {typeid} 第 {page} 页无内容，视为到底")
                    break

                targets = []
                for tid in tids:
                    if tid in seen_tids:
                        continue
                    seen_tids.add(tid)
                    targets.append(
                        CrawlTarget(
                            key=str(tid),
                            url=source.detail_url(tid),
                            partition=typeid,
                            metadata={
                                "tid": tid,
                                "typeid": typeid,
                                "section": section,
                            },
                        )
                    )

                page_run = engine.run(
                    FixedTargetSource(source, targets),
                    repository,
                    dry_run=dry_run,
                )
                summary["pages_scanned"] += 1
                summary["discovered"] += len(targets)
                summary["existing"] += repository.existing_count
                summary["requested"] += page_run.requested
                summary["saved"] += page_run.saved
                summary["failed"] += page_run.failed
                partition_summary["pages"] += 1
                partition_summary["saved"] += page_run.saved
                partition_summary["failed"] += page_run.failed

                if not dry_run:
                    checkpoints.save("x1080x", typeid, page)

        log.info(
            "x1080x 分页补抓结束: "
            f"pages={summary['pages_scanned']} discovered={summary['discovered']} "
            f"existing={summary['existing']} requested={summary['requested']} "
            f"saved={summary['saved']} failed={summary['failed']}"
        )
        return summary
