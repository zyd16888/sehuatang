"""
Web爬虫核心模块
负责协调整个数据抓取流程
"""
import asyncio
import time
from typing import List, Dict, Any, Optional
from drissio import BrowserAutomation
from util.log_util import log
from util.config import domain, page_num, date, proxy, proxy_enable
from .page_parser import PageParser
from .data_processor import DataProcessor
from .data_manager import DataManager
from .notification_manager import NotificationManager


class WebScraper:
    """Web爬虫主类"""

    def __init__(self):
        self.log = log
        self.browser = BrowserAutomation(
            proxy_enable=proxy_enable, proxy_url=proxy)
        self.page_parser = PageParser()
        self.data_processor = DataProcessor()
        self.data_manager = DataManager()
        self.notification_manager = NotificationManager()

    async def crawl_forum_section(self, fid: int) -> str:
        """
        爬取论坛板块数据
        
        Args:
            fid: 板块ID
            
        Returns:
            爬取结果消息
        """
        self.log.info(f"开始爬取板块 {fid}")

        try:
            # 第一阶段：获取板块页面信息
            plate_info_list, tid_list = await self._get_plate_info_batch(fid)

            if not tid_list:
                self.log.info(f"板块 {fid} 没有找到符合条件的帖子")
                return "没有新的数据"

            self.log.info(f"即将开始爬取的页面: {' '.join(tid_list)}")

            # 第二阶段：过滤已存在的数据
            new_tid_list, new_info_list = self.data_manager.compare_existing_data(
                tid_list, fid, plate_info_list
            )

            if not new_tid_list:
                self.log.info(f"板块 {fid} 没有新数据需要爬取")
                return "没有新的数据"

            self.log.info(f"需要爬取的页面: {' '.join(new_tid_list)}")

            # 第三阶段：获取帖子详细信息
            detailed_data_list = await self._get_thread_details_batch(new_info_list)

            if not detailed_data_list:
                self.log.info(f"板块 {fid} 没有获取到有效的详细数据")
                return "没有新的数据"

            self.log.info(f"本次抓取的数据条数为: {len(detailed_data_list)}")

            # 第四阶段：保存数据
            self.log.info("开始写入数据库")
            filtered_data = self.data_manager.filter_and_save_data(
                detailed_data_list, fid)

            # 第五阶段：发送通知
            return self.notification_manager.send_notifications(filtered_data, fid)

        except Exception as e:
            self.log.error(f"爬取板块 {fid} 时出错: {e}")
            return f"爬取失败: {str(e)}"

    async def _get_plate_info_batch(self, fid: int) -> tuple[List[Dict[str, Any]], List[str]]:
        """
        批量获取板块页面信息
        
        Args:
            fid: 板块ID
            
        Returns:
            tuple: (帖子信息列表, 帖子ID列表)
        """
        start_time = time.time()

        # 创建异步任务
        tasks = [
            self._get_plate_info(fid, page)
            for page in range(1, page_num + 1)
        ]

        # 执行异步任务
        results = await asyncio.gather(*tasks)

        end_time = time.time()
        self.log.info(f"get_plate_info 执行时间: {end_time - start_time:.2f}秒")

        # 合并结果
        all_info_list = []
        all_tid_list = []

        for info_list, tid_list in results:
            all_info_list.extend(info_list)
            all_tid_list.extend(tid_list)

        return all_info_list, all_tid_list

    async def _get_plate_info(self, fid: int, page: int) -> tuple[List[Dict[str, Any]], List[str]]:
        """
        获取单个板块页面信息
        
        Args:
            fid: 板块ID
            page: 页码
            
        Returns:
            tuple: (帖子信息列表, 帖子ID列表)
        """
        self.log.info(f"爬取板块 {fid} 第 {page} 页")

        url = f"https://{domain}/forum-{fid}-{page}.html"

        try:
            html_response = self.browser.get_page_html(url)
            if not html_response:
                self.log.warning(f"获取页面内容失败: {url}")
                return [], []

            return self.page_parser.parse_plate_page(html_response, date())

        except Exception as e:
            self.log.error(f"获取板块页面信息时出错: {e}")
            return [], []

    async def _get_thread_details_batch(self, info_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        批量获取帖子详细信息

        Args:
            info_list: 帖子基本信息列表

        Returns:
            详细信息列表
        """
        start_time = time.time()

        # 创建异步任务
        tasks = [
            self._get_thread_detail(info["tid"], info)
            for info in info_list
        ]

        # 执行异步任务
        results = await asyncio.gather(*tasks)

        end_time = time.time()
        self.log.info(f"get_thread_details 执行时间: {end_time - start_time:.2f}秒")

        # 处理结果
        detailed_data = self.data_processor.merge_thread_data(
            results, info_list)
        cleaned_data = self.data_processor.clean_data(detailed_data)

        return cleaned_data

    async def _get_thread_detail(self, tid: str, thread_info: Dict[str, Any]) -> Optional[tuple]:
        """
        获取单个帖子的详细信息

        Args:
            tid: 帖子ID
            thread_info: 帖子基本信息

        Returns:
            tuple: (详细信息, 基本信息) 或 None
        """
        url = f"https://{domain}/?mod=viewthread&tid={tid}"

        try:
            html_response = self.browser.get_page_html(url)
            if not html_response:
                self.log.warning(f"获取帖子页面内容失败: {url}")
                return None

            detailed_data = self.page_parser.parse_thread_page(html_response)
            if detailed_data:
                self.log.debug(f"成功爬取帖子 {tid}")
                return detailed_data, thread_info
            else:
                self.log.warning(f"解析帖子页面失败: {tid}")
                return None

        except Exception as e:
            self.log.error(f"获取帖子 {tid} 详细信息时出错: {e}")
            return None

    def initialize_homepage(self) -> bool:
        """
        初始化主页，进行必要的登录或验证

        Returns:
            是否成功初始化
        """
        try:
            self.log.debug(f"浏览主页: {domain}")
            html_response = self.browser.get_page_html(f"https://{domain}")

            if html_response:
                # 简单验证页面是否正常加载
                if domain.upper() in html_response or "forum" in html_response.lower():
                    self.log.info("主页初始化成功")
                    return True
                else:
                    self.log.warning("主页内容异常")
                    return False
            else:
                self.log.error("无法获取主页内容")
                return False

        except Exception as e:
            self.log.error(f"初始化主页时出错: {e}")
            return False

    def close(self):
        """关闭爬虫，释放资源"""
        try:
            self.browser.close_page()
            self.log.info("爬虫资源已释放")
        except Exception as e:
            self.log.error(f"关闭爬虫时出错: {e}")

    def __enter__(self):
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.close()
        # 不抑制异常
        return False
