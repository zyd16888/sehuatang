import time
import uuid
from typing import Optional

from util.log_util import log

from .contracts import (
    CrawlContext,
    CrawlFailure,
    DiscoveryResult,
    FailureStore,
    NullFailureStore,
    RecordRepository,
    SourceAdapter,
)
from .http import CrawlerHttpClient, redact_url
from .models import RunSummary


class CrawlEngine:
    """执行来源发现、目标筛选、详情抓取、解析和保存。"""

    def __init__(
        self,
        http: CrawlerHttpClient,
        failure_store: Optional[FailureStore] = None,
    ):
        self.http = http
        self.failure_store = failure_store or NullFailureStore()

    def run(
        self,
        source: SourceAdapter,
        repository: RecordRepository,
        *,
        dry_run: bool = False,
        retry_failed: bool = False,
        run_id: Optional[str] = None,
    ) -> RunSummary:
        context = CrawlContext(
            source=source.name,
            run_id=run_id or uuid.uuid4().hex,
            dry_run=dry_run,
            retry_failed=retry_failed,
        )
        started = time.monotonic()
        summary = RunSummary(source=source.name, run_id=context.run_id)

        if retry_failed:
            discovery = DiscoveryResult(
                targets=self.failure_store.due_targets(source.name),
                details={"retry_failed": True},
            )
        else:
            discovery = source.discover(context, self.http)
        summary.discovered = len(discovery.targets)
        summary.failed = discovery.failed
        summary.details.update(discovery.details)
        summary.retries = int(discovery.details.get("discovery_retries", 0))

        targets = (
            list(discovery.targets)
            if retry_failed
            else repository.select_targets(discovery.targets)
        )
        summary.requested = len(targets)
        fetch_results = self.http.fetch_many(
            [target.url for target in targets],
            stage="detail",
        )

        records = []
        failures = []
        for target, result in zip(targets, fetch_results):
            summary.retries += max(0, result.attempts - 1)
            if not result.ok:
                failures.append(
                    CrawlFailure(
                        source=source.name,
                        key=target.key,
                        url=target.url,
                        stage="fetch",
                        attempts=result.attempts,
                        error_type=result.error_type or "request_failed",
                        error_message=result.error_message or "",
                        metadata=target.metadata,
                    )
                )
                log.warning(
                    "详情请求终态失败: "
                    f"run_id={context.run_id} source={source.name} stage=detail "
                    f"target={target.key} attempts={result.attempts} "
                    f"error_type={result.error_type} url={redact_url(target.url)}"
                )
                continue

            try:
                record = source.parse_detail(target, result)
            except Exception as exc:
                log.warning(
                    "详情解析异常: "
                    f"run_id={context.run_id} source={source.name} "
                    f"target={target.key} error_type={type(exc).__name__.lower()}"
                )
                failures.append(
                    CrawlFailure(
                        source=source.name,
                        key=target.key,
                        url=target.url,
                        stage="parse",
                        attempts=result.attempts,
                        error_type=type(exc).__name__.lower(),
                        error_message=str(exc),
                        metadata=target.metadata,
                    )
                )
                continue
            if record is None:
                log.warning(
                    "详情文档校验失败: "
                    f"run_id={context.run_id} source={source.name} "
                    f"target={target.key}"
                )
                failures.append(
                    CrawlFailure(
                        source=source.name,
                        key=target.key,
                        url=target.url,
                        stage="parse",
                        attempts=result.attempts,
                        error_type="invalid_document",
                        metadata=target.metadata,
                    )
                )
                continue
            records.append(record)

        summary.succeeded = len(records)
        summary.failed += len(failures)

        if records and not dry_run:
            saved = repository.save_many(records)
            summary.saved = saved.saved
            summary.updated = saved.updated

        if failures and not dry_run:
            try:
                self.failure_store.record(failures)
            except Exception as exc:
                log.error(
                    "失败台账写入失败: "
                    f"run_id={context.run_id} source={source.name} error={exc}"
                )
        if records and not dry_run:
            try:
                self.failure_store.clear(
                    source.name,
                    [record.target.key for record in records],
                )
            except Exception as exc:
                log.error(
                    "失败台账清理失败: "
                    f"run_id={context.run_id} source={source.name} error={exc}"
                )

        summary.elapsed_ms = int((time.monotonic() - started) * 1000)
        log.info(
            "来源抓取结束: "
            f"run_id={context.run_id} source={source.name} status={summary.status.value} "
            f"discovered={summary.discovered} requested={summary.requested} "
            f"succeeded={summary.succeeded} failed={summary.failed} "
            f"saved={summary.saved} updated={summary.updated} "
            f"retries={summary.retries} elapsed_ms={summary.elapsed_ms}"
        )
        return summary
