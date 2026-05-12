"""
Web 爬虫核心模块（HTTP 版）
- 抓取层走 curl_cffi（见 scrapers/http_client.py），不再依赖浏览器
- 列表页 / 详情页用 ThreadPoolExecutor 并发拉取
"""
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from util.log_util import log
from util.config import date, domain, page_num
from util.read_config import get_config

from .data_manager import DataManager
from .data_processor import DataProcessor
from .http_client import http_client
from .notification_manager import NotificationManager
from .page_parser import PageParser


class WebScraper:
    """Web 爬虫主类"""

    def __init__(self):
        self.log = log
        self.http = http_client
        self.workers = int(get_config("concurrent_workers", 6) or 6)

        self.page_parser = PageParser()
        self.data_processor = DataProcessor()
        self.data_manager = DataManager()
        self.notification_manager = NotificationManager()

    # ---------- 对外入口 ----------

    async def crawl_forum_section(self, fid: int) -> str:
        self.log.info(f"开始爬取板块 {fid}")
        try:
            plate_info_list, tid_list = self._get_plate_info_batch(fid)
            if not tid_list:
                self.log.info(f"板块 {fid} 没有找到符合条件的帖子")
                return "没有新的数据"

            self.log.info(f"即将开始爬取的页面: {' '.join(tid_list)}")

            new_tid_list, new_info_list = self.data_manager.compare_existing_data(
                tid_list, fid, plate_info_list
            )
            if not new_tid_list:
                self.log.info(f"板块 {fid} 没有新数据需要爬取")
                return "没有新的数据"

            self.log.info(f"需要爬取的页面: {' '.join(new_tid_list)}")

            detailed_data_list = self._get_thread_details_batch(new_info_list)
            if not detailed_data_list:
                self.log.info(f"板块 {fid} 没有获取到有效的详细数据")
                return "没有新的数据"

            self.log.info(f"本次抓取的数据条数为: {len(detailed_data_list)}")

            self.log.info("开始写入数据库")
            filtered_data = self.data_manager.filter_and_save_data(detailed_data_list, fid)

            return self.notification_manager.send_notifications(filtered_data, fid)
        except Exception as e:
            self.log.error(f"爬取板块 {fid} 时出错: {e}")
            return f"爬取失败: {str(e)}"

    def initialize_homepage(self) -> bool:
        """HTTP 模式无需浏览器预热，保留接口兼容 main.py。"""
        return True

    # ---------- 内部 ----------

    def _get_plate_info_batch(self, fid: int) -> Tuple[List[Dict[str, Any]], List[str]]:
        t0 = time.time()
        urls = [f"https://{domain}/forum-{fid}-{p}.html" for p in range(1, page_num + 1)]
        self.log.info(f"正在批量请求 {len(urls)} 个板块页面（并发 {self.workers}）...")
        responses = self._fetch_many(urls)

        all_info: List[Dict[str, Any]] = []
        all_tids: List[str] = []
        target_date = date()

        for page, body in enumerate(responses, 1):
            if not body:
                self.log.warning(f"获取板块 {fid} 第 {page} 页内容失败")
                continue
            try:
                info_list, tid_list = self.page_parser.parse_plate_page(body, target_date)
                all_info.extend(info_list)
                all_tids.extend(tid_list)
                self.log.info(f"成功解析板块 {fid} 第 {page} 页，获得 {len(info_list)} 个帖子")
            except Exception as e:
                self.log.error(f"解析板块 {fid} 第 {page} 页时出错: {e}")

        self.log.info(f"_get_plate_info_batch 执行时间: {time.time() - t0:.2f}秒")
        return all_info, all_tids

    def _get_thread_details_batch(self, info_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        t0 = time.time()
        urls = [f"https://{domain}/forum.php?mod=viewthread&tid={info['tid']}" for info in info_list]
        self.log.info(f"正在批量请求 {len(urls)} 个帖子详情页（并发 {self.workers}）...")
        responses = self._fetch_many(urls)

        results: List[Optional[tuple]] = []
        for i, body in enumerate(responses):
            tid = info_list[i]["tid"]
            if not body:
                self.log.warning(f"获取帖子页面内容失败: {tid}")
                results.append(None)
                continue
            try:
                detail = self.page_parser.parse_thread_page(body)
                if detail:
                    results.append((detail, info_list[i]))
                    self.log.debug(f"成功解析帖子 {tid}")
                else:
                    results.append(None)
                    self.log.warning(f"解析帖子页面失败: {tid}")
            except Exception as e:
                self.log.error(f"解析帖子 {tid} 时出错: {e}")
                results.append(None)

        self.log.info(f"_get_thread_details_batch 执行时间: {time.time() - t0:.2f}秒")

        merged = self.data_processor.merge_thread_data(results, info_list)
        return self.data_processor.clean_data(merged)

    def _fetch_many(self, urls: List[str]) -> List[Optional[bytes]]:
        """按输入顺序返回结果。同一 URL 失败位置为 None。"""
        if not urls:
            return []
        results: List[Optional[bytes]] = [None] * len(urls)
        with ThreadPoolExecutor(max_workers=min(self.workers, len(urls))) as pool:
            futures = {pool.submit(self.http.get_html, u): i for i, u in enumerate(urls)}
            for fut in futures:
                idx = futures[fut]
                try:
                    results[idx] = fut.result()
                except Exception as e:
                    self.log.error(f"抓取异常 idx={idx} url={urls[idx]}: {e}")
                    results[idx] = None
        return results

    # ---------- 上下文 ----------

    def close(self):
        self.log.info("爬虫资源已释放")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
