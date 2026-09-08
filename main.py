import asyncio
from scrapers.registry import source_registry
from scrapers.web_scraper import WebScraper
from util.log_util import log
from util.config import fid_list
from util.read_config import get_config


async def crawl_forum_section(fid: int) -> dict:
    """
    爬取论坛板块数据的入口函数

    Args:
        fid: 板块ID

    Returns:
        爬取结果消息
    """
    with WebScraper() as scraper:
        return await scraper.crawl_forum_section(fid)


async def crawl_sehuatang(dry_run: bool = False):
    """执行现有 Discuz 数据源抓取。"""
    from util.config import date

    log.info(f"开始执行 sehuatang 数据抓取，日期: {date()}")

    result = await source_registry.run(
        "sehuatang",
        get_config(),
        force=True,
        dry_run=dry_run,
    )
    log.info(f"sehuatang 数据抓取完成: {result}")
    return result


async def crawl_javbee(
    force: bool = False,
    dry_run: bool = False,
    retry_failed: bool = False,
):
    """执行独立 Javbee 数据源抓取。"""
    log.info("开始执行 Javbee 数据抓取")
    summary = await source_registry.run(
        "javbee",
        get_config(),
        force=force,
        dry_run=dry_run,
        retry_failed=retry_failed,
    )
    log.info(f"Javbee 数据抓取完成: {summary}")
    return summary


async def crawl_sources(
    sources=None,
    *,
    force: bool = False,
    dry_run: bool = False,
    retry_failed: bool = False,
):
    selected = list(sources or source_registry.names())
    return await source_registry.run_many(
        selected,
        get_config(),
        force=force,
        dry_run=dry_run,
        retry_failed=retry_failed,
    )


async def main(sources=None, dry_run: bool = False):
    """主函数，通过来源注册表执行已配置的数据源。"""
    results = await crawl_sources(sources, dry_run=dry_run)
    log.info("所有数据源处理完成，程序结束")
    return results


async def backfill_pages(source, start_page, end_page, *, fids=None, typeids=None,
                         resume=False, dry_run=False):
    if source not in {"sehuatang", "x1080x"}:
        raise ValueError(f"来源不支持分页补抓: {source}")
    description = f"{source} 补抓第 {start_page}–{end_page} 页"
    with source_registry.activity(source, "backfill", description) as acquired:
        if not acquired:
            log.warning(f"来源已在运行中，跳过补抓: source={source}")
            return False
        return await _backfill_pages(source, start_page, end_page, fids=fids,
                                     typeids=typeids, resume=resume, dry_run=dry_run)


async def _backfill_pages(
    source: str,
    start_page: int,
    end_page: int,
    *,
    fids=None,
    typeids=None,
    resume: bool = False,
    dry_run: bool = False,
) -> bool:
    """按页区间补抓历史数据；详情失败进失败台账，用 retry-failed 恢复。"""
    import asyncio as _asyncio

    log.info(
        f"开始 {source} 分页补抓: 第 {start_page}-{end_page} 页"
    )
    success = True
    if source == "x1080x":
        from scrapers.registry import _source_config
        from scrapers.x1080x_scraper import X1080XScraper

        # 与 registry 运行路径一致：合并顶层 x1080x 与 crawler.sources.x1080x，
        # 否则丢失 challenge.flaresolverr_url / concurrency 等来源级配置
        scraper = X1080XScraper(
            config=_source_config(get_config() or {}, "x1080x")
        )
        summary = await _asyncio.to_thread(
            scraper.backfill_pages,
            start_page,
            end_page,
            typeids=typeids,
            resume=resume,
            dry_run=dry_run,
        )
        log.info(f"x1080x 分页补抓完成: {summary}")
        success = not any(
            str(partition.get("stopped", "")).startswith("list_failed")
            for partition in summary.get("partitions", {}).values()
        )
    elif source == "sehuatang":
        selected_fids = [int(fid) for fid in (fids or fid_list)]
        with WebScraper(dry_run=dry_run) as scraper:
            for fid in selected_fids:
                try:
                    summary = await scraper.backfill_pages(
                        fid,
                        start_page,
                        end_page,
                        resume=resume,
                    )
                    log.info(f"板块 {fid} 分页补抓完成: {summary}")
                    if str(summary.get("stopped", "")).startswith("list_failed"):
                        success = False
                except Exception as e:
                    success = False
                    log.error(f"板块 {fid} 分页补抓失败: {e}")
    else:
        raise ValueError(f"来源不支持分页补抓: {source}")
    return success


async def backfill(
    year: int,
    fids=None,
    resume: bool = False,
    dry_run: bool = False,
) -> bool:
    """按年份补抓历史数据；未指定板块时使用配置中的全部板块。"""
    selected_fids = list(fids) if fids else list(fid_list)
    success = True
    log.info(f"开始执行 {year} 年历史补抓，板块: {selected_fids}")

    scraper_options = {"target_date": str(year)}
    if dry_run:
        scraper_options["dry_run"] = True
    with WebScraper(**scraper_options) as scraper:
        for fid in selected_fids:
            try:
                summary = await scraper.backfill_forum_section(
                    fid,
                    year,
                    resume=resume,
                )
                log.info(f"板块 {fid} 的 {year} 年历史补抓完成: {summary}")
            except Exception as e:
                success = False
                log.error(f"板块 {fid} 的 {year} 年历史补抓失败: {e}")

    log.info(f"{year} 年历史补抓任务结束")
    return success


def _run_standalone():
    from notifications.memory_queue import shutdown_notifications
    completed = False
    try:
        asyncio.run(main())
        completed = True
    finally:
        shutdown_notifications(drain=completed)


if __name__ == "__main__":
    _run_standalone()
