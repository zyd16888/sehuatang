import asyncio
from scrapers.web_scraper import WebScraper
from util.log_util import log
from util.config import fid_list


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


async def main():
    """主函数，协调整个爬取流程"""
    from util.config import date

    log.info(f"开始执行数据抓取任务，日期: {date()}")

    # 使用上下文管理器确保资源正确释放
    with WebScraper() as scraper:
        # 初始化主页
        if not scraper.initialize_homepage():
            log.error("主页初始化失败，退出程序")
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

    log.info("所有板块处理完成，程序结束")


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
