import random
import time
import threading
from typing import Optional, Dict, Any, List
from queue import Queue, Empty
from DrissionPage import ChromiumOptions, WebPage
from util.log_util import log
from util.config import domain, proxy_enable, proxy_url
from util.read_config import get_config


class BrowserAutomation:
    """
    浏览器自动化类
    负责管理浏览器实例和页面操作
    支持多标签页并发抓取

    新增功能：
    - 智能页面加载等待：替换随机等待为基于页面状态的智能等待
    - 可配置等待策略：支持"smart"(智能等待)和"random"(随机等待)两种模式
    - Cloudflare验证优化：智能检测验证完成状态
    - 页面导航等待：自动检测页面跳转完成

    配置参数：
    - wait_strategy: "smart" | "random" - 等待策略
    - doc_load_timeout: 文档加载超时时间（秒）
    - min_wait_time: 最小等待时间（秒）
    - max_wait_time: 最大等待时间（秒）
    """

    def __init__(self, proxy_enable: bool = False, proxy_url: Optional[str] = None, max_tabs: int = 3):
        """
        初始化浏览器自动化实例

        Args:
            proxy_enable: 是否启用代理
            proxy_url: 代理URL
            max_tabs: 最大标签页数量，默认3个
        """
        self.page_instance: Optional[WebPage] = None
        self.proxy_enable = proxy_enable
        self.proxy_url = proxy_url
        self.max_tabs = max_tabs

        # 多标签页管理
        self.tab_pool: Queue = Queue()
        self.active_tabs: List[WebPage] = []
        self.tab_lock = threading.Lock()
        self._tabs_initialized = False

        # 从配置文件获取浏览器配置
        self.browser_config = self._load_browser_config()

        # 初始化浏览器页面（保持向后兼容）
        try:
            self.initialize_page()
        except Exception as e:
            log.error(f"初始化浏览器页面失败: {e}")
            raise

    def _load_browser_config(self) -> Dict[str, Any]:
        """
        从配置文件加载浏览器配置

        Returns:
            浏览器配置字典
        """
        default_config = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "max_retries": 3,
            "sleep_range": [3, 5],  # 保留作为备用，但优先使用智能等待
            "page_load_timeout": 30,
            "cloudflare_timeout": 10,
            "wait_strategy": "smart",  # 等待策略: "smart"(智能等待) 或 "random"(随机等待)
            "doc_load_timeout": 15,  # 文档加载超时时间
            "min_wait_time": 1,  # 最小等待时间（秒）
            "max_wait_time": 3,  # 最大等待时间（秒）
            "arguments": [
                "--headless=new",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-setuid-sandbox",
                "--remote-debugging-port=9222",
                "--disable-web-security",
                "--ignore-certificate-errors"
            ]
        }

        try:
            browser_config = get_config("browser", default_config)
            # 合并默认配置和用户配置
            return {**default_config, **browser_config}
        except Exception as e:
            log.warning(f"加载浏览器配置失败，使用默认配置: {e}")
            return default_config

    def initialize_page(self) -> None:
        """初始化浏览器页面实例（单页面模式，保持向后兼容）"""
        if self.page_instance is None:
            co = self._create_chromium_options()

            # 创建WebPage实例
            try:
                self.page_instance = WebPage(chromium_options=co)
                log.info("浏览器页面实例初始化成功")
            except Exception as e:
                log.error(f"初始化WebPage实例失败: {e}")
                raise

    def _create_chromium_options(self) -> ChromiumOptions:
        """创建Chromium选项配置"""
        co = ChromiumOptions()

        # 设置代理
        if self.proxy_enable and self.proxy_url:
            log.debug(f"启用代理: {self.proxy_url}")
            co.set_proxy(self.proxy_url)

        # 设置User-Agent
        co.set_user_agent(self.browser_config["user_agent"])

        # 添加浏览器参数
        for arg in self.browser_config["arguments"]:
            co.set_argument(arg)

        log.debug(f"浏览器选项配置完成: {co.arguments}")
        return co

    def _wait_for_page_load(self, page: WebPage, url: str) -> None:
        """
        智能等待页面加载完成

        Args:
            page: 页面实例
            url: 访问的URL，用于日志记录
        """
        wait_strategy = self.browser_config.get("wait_strategy", "smart")

        if wait_strategy == "smart":
            self._smart_wait_for_page_load(page, url)
        else:
            self._random_wait_for_page_load()

    def _smart_wait_for_page_load(self, page: WebPage, url: str) -> None:
        """
        智能等待页面加载完成

        Args:
            page: 页面实例
            url: 访问的URL，用于日志记录
        """
        try:
            doc_timeout = self.browser_config.get("doc_load_timeout", 15)
            min_wait = self.browser_config.get("min_wait_time", 1)
            max_wait = self.browser_config.get("max_wait_time", 3)

            log.debug(f"开始智能等待页面加载: {url}")

            # 等待文档加载完成
            try:
                page.wait.doc_loaded(timeout=doc_timeout)
                log.debug(f"文档加载完成: {url}")
            except Exception as e:
                log.warning(f"等待文档加载超时，继续执行: {e}")

            # 检查页面标题是否已加载
            title_loaded = False
            for _ in range(5):  # 最多检查5次
                try:
                    title = page.title
                    if title and title.strip() and title != "":
                        title_loaded = True
                        log.debug(f"页面标题已加载: {title}")
                        break
                except Exception:
                    pass
                time.sleep(0.5)

            if not title_loaded:
                log.warning(f"页面标题未能正常加载: {url}")

            # 最小等待时间，确保页面稳定
            wait_time = random.uniform(min_wait, max_wait)
            time.sleep(wait_time)
            log.debug(f"智能等待完成，总等待时间: {wait_time:.2f}秒")

        except Exception as e:
            log.error(f"智能等待过程中出错: {e}，回退到随机等待")
            self._random_wait_for_page_load()

    def _random_wait_for_page_load(self) -> None:
        """
        随机等待页面加载（备用方案）
        """
        sleep_range = self.browser_config["sleep_range"]
        sleep_duration = random.uniform(sleep_range[0], sleep_range[1])
        time.sleep(sleep_duration)
        log.debug(f"随机等待完成: {sleep_duration:.2f}秒")

    def _wait_for_cloudflare_completion(self, page: WebPage) -> None:
        """
        等待Cloudflare验证完成

        Args:
            page: 页面实例
        """
        try:
            max_wait_time = 15  # 最大等待15秒
            check_interval = 0.5  # 每0.5秒检查一次
            elapsed_time = 0

            log.debug("开始等待Cloudflare验证完成")

            while elapsed_time < max_wait_time:
                try:
                    current_title = page.title
                    # 如果标题不再是"Just a moment..."，说明验证完成
                    if current_title and current_title != "Just a moment...":
                        log.debug(f"Cloudflare验证完成，新标题: {current_title}")
                        time.sleep(1)  # 额外等待1秒确保页面稳定
                        return
                except Exception:
                    pass

                time.sleep(check_interval)
                elapsed_time += check_interval

            log.warning(f"Cloudflare验证等待超时 ({max_wait_time}秒)")

        except Exception as e:
            log.error(f"等待Cloudflare验证完成时出错: {e}")
            time.sleep(3)  # 回退到固定等待

    def _wait_for_page_navigation(self, page: WebPage) -> None:
        """
        等待页面导航完成

        Args:
            page: 页面实例
        """
        try:
            max_wait_time = 10  # 最大等待10秒
            check_interval = 0.5  # 每0.5秒检查一次
            elapsed_time = 0

            log.debug("开始等待页面导航完成")

            # 记录初始URL
            try:
                initial_url = page.url
            except Exception:
                initial_url = None

            while elapsed_time < max_wait_time:
                try:
                    current_url = page.url
                    current_title = page.title

                    # 检查URL是否发生变化或标题是否已加载
                    if (current_url and current_url != initial_url) or \
                       (current_title and current_title.strip() and current_title != domain.upper()):
                        log.debug(
                            f"页面导航完成，新URL: {current_url}, 新标题: {current_title}")
                        time.sleep(0.5)  # 额外等待0.5秒确保页面稳定
                        return
                except Exception:
                    pass

                time.sleep(check_interval)
                elapsed_time += check_interval

            log.warning(f"页面导航等待超时 ({max_wait_time}秒)")

        except Exception as e:
            log.error(f"等待页面导航完成时出错: {e}")
            time.sleep(2)  # 回退到固定等待

    def initialize_tabs(self) -> None:
        """初始化多标签页"""
        if self._tabs_initialized:
            return

        with self.tab_lock:
            if self._tabs_initialized:
                return

            try:
                # 创建主浏览器实例
                co = self._create_chromium_options()
                main_page = WebPage(chromium_options=co)

                # 创建多个标签页
                for i in range(self.max_tabs):
                    if i == 0:
                        # 第一个标签页就是主页面
                        tab = main_page
                    else:
                        # 创建新标签页
                        tab = main_page.new_tab()

                    self.active_tabs.append(tab)
                    self.tab_pool.put(tab)

                self._tabs_initialized = True
                log.info(f"成功初始化 {self.max_tabs} 个标签页")

            except Exception as e:
                log.error(f"初始化多标签页失败: {e}")
                # 清理已创建的标签页
                self._cleanup_tabs()
                raise

    def _get_available_tab(self, timeout: float = 30.0) -> Optional[WebPage]:
        """
        获取可用的标签页

        Args:
            timeout: 等待超时时间（秒）

        Returns:
            可用的标签页实例，超时返回None
        """
        try:
            return self.tab_pool.get(timeout=timeout)
        except Empty:
            log.warning(f"获取标签页超时 ({timeout}秒)")
            return None

    def _return_tab(self, tab: WebPage) -> None:
        """
        归还标签页到池中

        Args:
            tab: 要归还的标签页
        """
        try:
            self.tab_pool.put(tab, timeout=1.0)
        except Exception as e:
            log.error(f"归还标签页失败: {e}")

    def _cleanup_tabs(self) -> None:
        """清理所有标签页"""
        try:
            # 清空队列
            while not self.tab_pool.empty():
                try:
                    self.tab_pool.get_nowait()
                except Empty:
                    break

            # 关闭所有活动标签页
            for tab in self.active_tabs:
                try:
                    if hasattr(tab, 'quit'):
                        tab.quit()
                except Exception as e:
                    log.error(f"关闭标签页时出错: {e}")

            self.active_tabs.clear()
            self._tabs_initialized = False
            log.debug("所有标签页已清理")

        except Exception as e:
            log.error(f"清理标签页时出错: {e}")

    def get_page_html_multi_tab(self, url: str, max_retries: Optional[int] = None, tab_timeout: float = 30.0) -> str:
        """
        使用多标签页获取页面HTML内容

        Args:
            url: 目标URL
            max_retries: 最大重试次数，None时使用配置值
            tab_timeout: 获取标签页的超时时间

        Returns:
            页面HTML内容，失败时返回空字符串
        """
        # 确保多标签页已初始化
        if not self._tabs_initialized:
            self.initialize_tabs()

        if max_retries is None:
            max_retries = self.browser_config["max_retries"]

        retry_count = 0

        while retry_count < max_retries:
            tab = None
            try:
                # 获取可用标签页
                tab = self._get_available_tab(tab_timeout)
                if tab is None:
                    log.error(f"无法获取可用标签页，URL: {url}")
                    return ""

                # 使用标签页访问页面
                html_content = self._get_page_html_with_tab(tab, url)
                return html_content

            except Exception as e:
                retry_count += 1
                log.error(
                    f"使用多标签页获取页面时出错 (重试 {retry_count}/{max_retries}): {e}")

                if retry_count >= max_retries:
                    log.error(f"达到最大重试次数 ({max_retries})，返回空HTML")
                    return ""

            finally:
                # 归还标签页
                if tab is not None:
                    self._return_tab(tab)

        return ""

    def _get_page_html_with_tab(self, tab: WebPage, url: str) -> str:
        """
        使用指定标签页获取页面HTML

        Args:
            tab: 标签页实例
            url: 目标URL

        Returns:
            页面HTML内容
        """
        # 访问页面
        tab.get(url)
        log.debug(f"标签页访问页面: {url}")

        # 智能等待页面加载完成
        self._wait_for_page_load(tab, url)

        # 处理特殊页面情况
        self._handle_special_pages_for_tab(tab)

        # 获取并返回HTML内容
        return self._get_html_content_from_tab(tab)

    def _handle_special_pages_for_tab(self, tab: WebPage) -> None:
        """处理特殊页面情况（标签页版本）"""
        try:
            page_title = tab.title
            log.debug(f"标签页页面标题: {page_title}")

            # 处理Cloudflare验证
            if page_title == "Just a moment...":
                self._handle_cloudflare_challenge_for_tab(tab)

            # 处理域名入口页面
            if page_title == domain.upper():
                self._handle_domain_entrance_for_tab(tab)

        except Exception as e:
            log.error(f"处理特殊页面时出错: {e}")

    def _handle_cloudflare_challenge_for_tab(self, tab: WebPage) -> None:
        """处理Cloudflare验证（标签页版本）"""
        log.debug("标签页检测到Cloudflare验证，开始处理")

        try:
            # 获取验证框架
            frame = tab.get_frame(
                '@src^https://challenges.cloudflare.com/cdn-cgi')

            # 等待验证元素加载
            timeout = self.browser_config["cloudflare_timeout"]
            tab.wait.eles_loaded('.cb-i', timeout=timeout)
            time.sleep(2)  # 减少固定等待时间

            # 点击验证按钮
            checkbox = frame.ele('.cb-i')
            checkbox.click()

            # 等待页面开始加载
            tab.wait.load_start()

            # 智能等待验证完成
            self._wait_for_cloudflare_completion(tab)

            log.debug("标签页Cloudflare验证处理完成")

        except Exception as e:
            log.error(f"处理标签页Cloudflare验证时出错: {e}")
            raise

    def _handle_domain_entrance_for_tab(self, tab: WebPage) -> None:
        """处理域名入口页面（标签页版本）"""
        log.debug("标签页检测到域名入口页面，尝试点击进入")

        try:
            enter_button = tab.ele('.enter-btn')

            if enter_button:
                log.debug(f"标签页找到入口按钮: {enter_button.html}")
                time.sleep(0.5)  # 减少等待时间
                enter_button.click()

                # 等待页面跳转完成
                self._wait_for_page_navigation(tab)
                log.debug("标签页成功点击入口按钮")
            else:
                log.warning("标签页未找到入口按钮")

        except Exception as e:
            log.error(f"处理标签页域名入口页面时出错: {e}")
            raise

    def _get_html_content_from_tab(self, tab: WebPage) -> str:
        """
        从标签页获取HTML内容

        Args:
            tab: 标签页实例

        Returns:
            页面HTML内容
        """
        try:
            page_html = tab.html
            current_title = tab.title
            log.debug(f"成功从标签页获取HTML，当前标题: {current_title}")
            return page_html

        except Exception as e:
            log.error(f"从标签页获取HTML时出错: {e}")
            raise

    def get_page_html(self, url: str, max_retries: Optional[int] = None) -> str:
        """
        获取页面HTML内容，包含重试机制

        Args:
            url: 目标URL
            max_retries: 最大重试次数，None时使用配置值

        Returns:
            页面HTML内容，失败时返回空字符串
        """
        if max_retries is None:
            max_retries = self.browser_config["max_retries"]

        retry_count = 0

        while retry_count < max_retries:
            try:
                # 确保页面实例存在
                if self.page_instance is None:
                    self.initialize_page()

                # 访问页面
                self._navigate_to_page(url)

                # 处理特殊页面情况
                self._handle_special_pages()

                # 获取并返回HTML内容
                return self._get_html_content()

            except Exception as e:
                retry_count += 1
                log.error(f"获取页面时出错 (重试 {retry_count}/{max_retries}): {e}")

                # 重置浏览器实例
                self._reset_browser_instance()

                if retry_count >= max_retries:
                    log.error(f"达到最大重试次数 ({max_retries})，返回空HTML")
                    return ""

        return ""

    def _navigate_to_page(self, url: str) -> None:
        """
        导航到指定页面

        Args:
            url: 目标URL
        """
        self.page_instance.get(url)
        log.debug(f"访问页面: {url}")

        # 智能等待页面加载完成
        self._wait_for_page_load(self.page_instance, url)

    def _handle_special_pages(self) -> None:
        """处理特殊页面情况（Cloudflare验证、入口页面等）"""
        try:
            page_title = self.page_instance.title
            log.debug(f"页面标题: {page_title}")

            # 处理Cloudflare验证
            if page_title == "Just a moment...":
                self._handle_cloudflare_challenge()

            # 处理域名入口页面
            if page_title == domain.upper():
                self._handle_domain_entrance()

        except Exception as e:
            log.error(f"处理特殊页面时出错: {e}")

    def _handle_cloudflare_challenge(self) -> None:
        """处理Cloudflare验证"""
        log.debug("检测到Cloudflare验证，开始处理")

        try:
            # 获取验证框架
            frame = self.page_instance.get_frame(
                '@src^https://challenges.cloudflare.com/cdn-cgi')

            # 等待验证元素加载
            timeout = self.browser_config["cloudflare_timeout"]
            self.page_instance.wait.eles_loaded('.cb-i', timeout=timeout)
            time.sleep(2)  # 减少固定等待时间

            # 点击验证按钮
            checkbox = frame.ele('.cb-i')
            checkbox.click()

            # 等待页面开始加载
            self.page_instance.wait.load_start()

            # 智能等待验证完成
            self._wait_for_cloudflare_completion(self.page_instance)

            log.debug("Cloudflare验证处理完成")

        except Exception as e:
            log.error(f"处理Cloudflare验证时出错: {e}")
            raise

    def _handle_domain_entrance(self) -> None:
        """处理域名入口页面"""
        log.debug("检测到域名入口页面，尝试点击进入")

        try:
            enter_button = self.page_instance.ele('.enter-btn')

            if enter_button:
                log.debug(f"找到入口按钮: {enter_button.html}")
                time.sleep(0.5)  # 减少等待时间
                enter_button.click()

                # 等待页面跳转完成
                self._wait_for_page_navigation(self.page_instance)
                log.debug("成功点击入口按钮")
            else:
                log.warning("未找到入口按钮")

        except Exception as e:
            log.error(f"处理域名入口页面时出错: {e}")
            raise

    def _get_html_content(self) -> str:
        """
        获取页面HTML内容

        Returns:
            页面HTML内容
        """
        try:
            page_html = self.page_instance.html
            current_title = self.page_instance.title
            log.debug(f"成功获取页面HTML，当前标题: {current_title}")
            return page_html

        except Exception as e:
            log.error(f"获取页面HTML时出错: {e}")
            raise

    def _reset_browser_instance(self) -> None:
        """重置浏览器实例"""
        self.close_page()
        time.sleep(2)  # 等待浏览器彻底关闭

    def close_page(self) -> None:
        """安全关闭页面实例"""
        # 清理多标签页
        if self._tabs_initialized:
            self._cleanup_tabs()

        # 关闭单页面实例（保持向后兼容）
        if self.page_instance is not None:
            try:
                self.page_instance.quit()
                log.debug("浏览器页面已关闭")
            except Exception as e:
                log.error(f"关闭浏览器页面时出错: {e}")
            finally:
                self.page_instance = None

    def get_multiple_pages_html(self, urls: List[str], max_retries: Optional[int] = None) -> List[str]:
        """
        批量获取多个页面的HTML内容（使用多标签页）

        Args:
            urls: URL列表
            max_retries: 最大重试次数

        Returns:
            HTML内容列表，与输入URL列表对应
        """
        if not urls:
            return []

        # 确保多标签页已初始化
        if not self._tabs_initialized:
            self.initialize_tabs()

        results = []

        # 使用线程池处理多个URL
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_tabs) as executor:
            # 提交所有任务
            future_to_url = {
                executor.submit(self.get_page_html_multi_tab, url, max_retries): url
                for url in urls
            }

            # 收集结果（保持顺序）
            url_to_result = {}
            for future in concurrent.futures.as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    html = future.result()
                    url_to_result[url] = html
                except Exception as e:
                    log.error(f"获取页面 {url} 时出错: {e}")
                    url_to_result[url] = ""

            # 按原始顺序返回结果
            results = [url_to_result.get(url, "") for url in urls]

        log.info(f"批量获取 {len(urls)} 个页面完成，成功 {sum(1 for r in results if r)} 个")
        return results

    def is_page_active(self) -> bool:
        """
        检查页面实例是否处于活动状态

        Returns:
            页面是否活动
        """
        try:
            return (self.page_instance is not None and
                    hasattr(self.page_instance, 'title') and
                    self.page_instance.title is not None)
        except Exception:
            return False

    def get_current_url(self) -> Optional[str]:
        """
        获取当前页面URL

        Returns:
            当前URL，失败时返回None
        """
        try:
            if self.page_instance:
                return self.page_instance.url
        except Exception as e:
            log.error(f"获取当前URL时出错: {e}")
        return None

    def get_page_title(self) -> Optional[str]:
        """
        获取当前页面标题

        Returns:
            页面标题，失败时返回None
        """
        try:
            if self.page_instance:
                return self.page_instance.title
        except Exception as e:
            log.error(f"获取页面标题时出错: {e}")
        return None

    # 上下文管理器支持
    def __enter__(self):
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.close_page()
        if exc_type is not None:
            log.error(f"上下文管理器中发生异常: {exc_type.__name__}: {exc_val}")
        return False


# 示例用法
if __name__ == "__main__":
    # 单页面模式（向后兼容）
    print("=== 单页面模式示例 ===")
    with BrowserAutomation(proxy_enable=proxy_enable, proxy_url=proxy_url) as browser:
        url = "https://sehuatang.org"
        html = browser.get_page_html(url)
        print(f"页面标题: {browser.get_page_title()}")
        print(f"当前URL: {browser.get_current_url()}")
        print(f"HTML长度: {len(html)}")

    # 多标签页模式示例
    print("\n=== 多标签页模式示例 ===")
    with BrowserAutomation(proxy_enable=proxy_enable, proxy_url=proxy_url, max_tabs=3) as browser:
        # 单个页面使用多标签页
        url = "https://sehuatang.org"
        html = browser.get_page_html_multi_tab(url)
        print(f"多标签页HTML长度: {len(html)}")

        # 批量获取多个页面
        urls = [
            "https://sehuatang.org/forum-103-1.html",
            "https://sehuatang.org/forum-103-2.html",
            "https://sehuatang.org/forum-104-1.html"
        ]
        html_list = browser.get_multiple_pages_html(urls)
        print(f"批量获取结果: {[len(html) for html in html_list]}")

    # 传统用法（仍然支持）
    # browser = BrowserAutomation(proxy_enable=proxy_enable, proxy_url=proxy_url)
    # try:
    #     html = browser.get_page_html("https://sehuatang.org")
    #     print(html)
    # finally:
    #     browser.close_page()
