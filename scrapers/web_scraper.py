"""
Web爬虫核心模块
负责协调整个数据抓取流程
"""
import asyncio
import time
from typing import List, Dict, Any, Optional
from drissio_clean import BrowserAutomation
from util.log_util import log
from util.config import domain, page_num, date, proxy, proxy_enable
from util.read_config import get_config
from .page_parser import PageParser
from .data_processor import DataProcessor
from .data_manager import DataManager
from .notification_manager import NotificationManager


class WebScraper:
    """Web爬虫主类"""

    def __init__(self):
        self.log = log

        # 从配置文件获取浏览器配置
        browser_config = get_config("browser", {})
        max_tabs = browser_config.get("max_tabs", 3)
        enable_multi_tab = browser_config.get("enable_multi_tab", True)

        # 初始化浏览器自动化实例
        self.browser = BrowserAutomation(
            max_tabs=max_tabs
        )

        # 存储配置
        self.enable_multi_tab = enable_multi_tab
        self.max_tabs = max_tabs

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
        """
        start_time = time.time()

        # 直接使用多标签页批量获取，不再区分模式
        urls = [
            f"https://{domain}/forum-{fid}-{page}.html" for page in range(1, page_num + 1)]
        
        self.log.info(f"正在批量请求 {len(urls)} 个板块页面...")
        html_responses = self.browser.get_batch_html(urls)

        # 解析所有页面
        all_info_list = []
        all_tid_list = []

        for page, html_response in enumerate(html_responses, 1):
            if html_response:
                try:
                    info_list, tid_list = self.page_parser.parse_plate_page(
                        html_response, date())
                    all_info_list.extend(info_list)
                    all_tid_list.extend(tid_list)
                    self.log.info(
                        f"成功解析板块 {fid} 第 {page} 页，获得 {len(info_list)} 个帖子")
                except Exception as e:
                    self.log.error(f"解析板块 {fid} 第 {page} 页时出错: {e}")
            else:
                self.log.warning(f"获取板块 {fid} 第 {page} 页内容失败")

        end_time = time.time()
        self.log.info(f"get_plate_info 执行时间: {end_time - start_time:.2f}秒")

        return all_info_list, all_tid_list

    async def _get_thread_details_batch(self, info_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        批量获取帖子详细信息
        """
        start_time = time.time()

        # 直接使用多标签页批量获取
        urls = [
            f"https://{domain}/?mod=viewthread&tid={info['tid']}" for info in info_list]
        
        self.log.info(f"正在批量请求 {len(urls)} 个帖子详情页...")
        html_responses = self.browser.get_batch_html(urls)

        # 解析所有页面
        results = []
        for i, html_response in enumerate(html_responses):
            if html_response:
                try:
                    detailed_data = self.page_parser.parse_thread_page(
                        html_response)
                    if detailed_data:
                        results.append((detailed_data, info_list[i]))
                        self.log.debug(f"成功解析帖子 {info_list[i]['tid']}")
                    else:
                        results.append(None)
                        self.log.warning(
                            f"解析帖子页面失败: {info_list[i]['tid']}")
                except Exception as e:
                    self.log.error(f"解析帖子 {info_list[i]['tid']} 时出错: {e}")
                    results.append(None)
            else:
                self.log.warning(f"获取帖子页面内容失败: {info_list[i]['tid']}")
                results.append(None)

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
            html_response = self.browser.get_html(url)
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
            self.log.info(f"开始初始化主页: {domain}")

            # 尝试多次获取主页内容
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                self.log.info(f"第 {attempt}/{max_attempts} 次尝试获取主页内容")

                try:
                    html_response = self.browser.get_html(
                        f"https://{domain}")

                    # 详细记录响应信息
                    if html_response:
                        html_length = len(html_response)
                        self.log.info(f"获取到HTML内容，长度: {html_length} 字符")

                        # 记录HTML内容的前500个字符用于调试
                        preview = html_response[:500] if len(
                            html_response) > 500 else html_response
                        self.log.debug(f"HTML内容预览: {preview}")

                        # 检查页面内容是否有效
                        if self._validate_homepage_content(html_response):
                            self.log.info("主页初始化成功")
                            return True
                        else:
                            self.log.warning(f"第 {attempt} 次尝试：主页内容验证失败")
                            if attempt < max_attempts:
                                self.log.info("等待3秒后重试...")
                                time.sleep(3)
                                continue
                    else:
                        self.log.warning(f"第 {attempt} 次尝试：获取到空的HTML内容")
                        if attempt < max_attempts:
                            self.log.info("等待3秒后重试...")
                            time.sleep(3)
                            continue

                except Exception as e:
                    self.log.error(f"第 {attempt} 次尝试获取主页时出错: {e}")
                    if attempt < max_attempts:
                        self.log.info("等待3秒后重试...")
                        time.sleep(3)
                        continue

            self.log.error(f"经过 {max_attempts} 次尝试，仍无法成功初始化主页")
            return False

        except Exception as e:
            self.log.error(f"初始化主页时发生未预期的错误: {e}")
            return False

    def _validate_homepage_content(self, html_content: str) -> bool:
        """
        验证主页内容是否有效

        Args:
            html_content: HTML内容

        Returns:
            是否有效
        """
        try:
            if not html_content or len(html_content.strip()) < 100:
                self.log.warning("HTML内容过短，可能无效")
                return False

            # 转换为小写进行检查
            html_lower = html_content.lower()
            domain_lower = domain.lower()

            # 检查多个验证条件
            validation_checks = [
                domain_lower in html_lower,
                "forum" in html_lower,
                "<html" in html_lower,
                "<body" in html_lower,
                "sehuatang" in html_lower
            ]

            passed_checks = sum(validation_checks)
            self.log.debug(f"内容验证结果: {passed_checks}/5 项检查通过")

            # 至少需要通过2项检查
            if passed_checks >= 2:
                self.log.info("主页内容验证通过")
                return True
            else:
                self.log.warning(f"主页内容验证失败，仅通过 {passed_checks}/5 项检查")
                # 记录更多调试信息
                self.log.debug(f"检查结果详情:")
                self.log.debug(
                    f"- 包含域名 '{domain_lower}': {domain_lower in html_lower}")
                self.log.debug(f"- 包含 'forum': {'forum' in html_lower}")
                self.log.debug(f"- 包含 '<html': {'<html' in html_lower}")
                self.log.debug(f"- 包含 '<body': {'<body' in html_lower}")
                self.log.debug(
                    f"- 包含 'sehuatang': {'sehuatang' in html_lower}")
                return False

        except Exception as e:
            self.log.error(f"验证主页内容时出错: {e}")
            return False

    def close(self):
        """关闭爬虫，释放资源"""
        try:
            self.browser.close()
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
