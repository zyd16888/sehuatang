import time
import uuid
from typing import Optional

from util.log_util import log

from .contracts import (
    CrawlContext,
    CrawlFailure,
    DiscoveryResult,
    DetailValidationError,
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
        batch_size: int = 20,
    ) -> RunSummary:
        logger = log.bind(module=source.name)
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

        # 分批抓取并入库：一批抓完立即保存，运行中途被杀
        # （重启/断电）时已完成批次不丢失。
        batch_size = max(1, int(batch_size))
        for start in range(0, len(targets), batch_size):
            batch = targets[start:start + batch_size]
            self._process_batch(context, source, repository, batch, summary)
            if summary.details.get("rate_limited"):
                break

        summary.elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "来源抓取结束: "
            f"run_id={context.run_id} source={source.name} status={summary.status.value} "
            f"discovered={summary.discovered} requested={summary.requested} "
            f"succeeded={summary.succeeded} failed={summary.failed} "
            f"saved={summary.saved} updated={summary.updated} "
            f"retries={summary.retries} elapsed_ms={summary.elapsed_ms}"
        )
        return summary

    def _process_batch(
        self,
        context: CrawlContext,
        source: SourceAdapter,
        repository: RecordRepository,
        targets,
        summary: RunSummary,
    ) -> None:
        """抓取、解析并保存一批目标，累加进汇总。"""
        logger = log.bind(module=source.name)
        fetch_results = self.http.fetch_many(
            [target.url for target in targets],
            stage="detail",
        )

        records = []
        failures = []
        for target, result in zip(targets, fetch_results):
            summary.retries += max(0, result.attempts - 1)
            if result.error_type == "rate_limited":
                summary.failed += 1
                summary.details["rate_limited"] = True
                # 站点级暂时阻塞不计入单帖失败次数；补抓客户端会在返回前恢复。
                continue
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
                logger.warning(
                    "详情请求终态失败: "
                    f"run_id={context.run_id} source={source.name} stage=detail "
                    f"target={target.key} attempts={result.attempts} "
                    f"error_type={result.error_type} url={redact_url(target.url)}"
                )
                continue

            try:
                record = source.parse_detail(target, result)
            except Exception as exc:
                error_type = (exc.reason if isinstance(exc, DetailValidationError)
                              else type(exc).__name__.lower())
                logger.warning(
                    "详情解析异常: "
                    f"run_id={context.run_id} source={source.name} "
                    f"target={target.key} error_type={error_type}"
                )
                failures.append(
                    CrawlFailure(
                        source=source.name,
                        key=target.key,
                        url=target.url,
                        stage="parse",
                        attempts=result.attempts,
                        error_type=error_type,
                        error_message=str(exc),
                        metadata=target.metadata,
                    )
                )
                continue
            if record is None:
                logger.warning(
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

        summary.succeeded += len(records)
        summary.failed += len(failures)

        if records and not context.dry_run:
            saved = repository.save_many(records)
            summary.saved += saved.saved
            summary.updated += saved.updated

        if failures and not context.dry_run:
            try:
                self.failure_store.record(failures)
            except Exception as exc:
                logger.error(
                    "失败台账写入失败: "
                    f"run_id={context.run_id} source={source.name} error={exc}"
                )
        if records and not context.dry_run:
            try:
                self.failure_store.clear(
                    source.name,
                    [record.target.key for record in records],
                )
            except Exception as exc:
                logger.error(
                    "失败台账清理失败: "
                    f"run_id={context.run_id} source={source.name} error={exc}"
                )
