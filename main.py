import asyncio
from scrapers.javbee_scraper import JavbeeScraper
from scrapers.web_scraper import WebScraper
from util.log_util import log
from util.config import fid_list
from util.read_config import get_config


async def crawl_forum_section(fid: int) -> str:
    """
    爬取论坛板块数据的入口函数

    Args:
        fid: 板块ID

    Returns:
        爬取结果消息
    """
    with WebScraper() as scraper:
        return await scraper.crawl_forum_section(fid)


async def crawl_sehuatang():
    """执行现有 Discuz 数据源抓取。"""
    from util.config import date

    log.info(f"开始执行 sehuatang 数据抓取，日期: {date()}")

    # 使用上下文管理器确保资源正确释放
    with WebScraper() as scraper:
        # 初始化主页
        if not scraper.initialize_homepage():
            log.error("sehuatang 主页初始化失败")
            return

        # 遍历所有板块进行爬取
        for fid in fid_list:
            try:
                log.info(f"开始处理板块 {fid}")
                result = await scraper.crawl_forum_section(fid)
                log.info(f"板块 {fid} 处理完成: {result}")
            except Exception as e:
                log.error(f"处理板块 {fid} 时出错: {e}")
                continue

    log.info("sehuatang 所有板块处理完成")


async def crawl_javbee(force: bool = False):
    """执行独立 Javbee 数据源抓取。"""
    config = get_config("javbee", {}) or {}
    if not force and not config.get("enable", False):
        log.info("Javbee 数据源未启用，跳过")
        return None

    if not get_config("mongodb.enable", False):
        raise RuntimeError("Javbee 数据源要求启用 MongoDB")

    log.info("开始执行 Javbee 数据抓取")
    summary = await asyncio.to_thread(JavbeeScraper(config=config).crawl)
    log.info(f"Javbee 数据抓取完成: {summary}")
    return summary


async def main():
    """主函数，顺序执行已配置的数据源。"""
    await crawl_sehuatang()
    await crawl_javbee()
    log.info("所有数据源处理完成，程序结束")


async def backfill(year: int, fids=None, resume: bool = False) -> bool:
    """按年份补抓历史数据；未指定板块时使用配置中的全部板块。"""
    selected_fids = list(fids) if fids else list(fid_list)
    success = True
    log.info(f"开始执行 {year} 年历史补抓，板块: {selected_fids}")

    with WebScraper(target_date=str(year)) as scraper:
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


if __name__ == "__main__":
    asyncio.run(main())
