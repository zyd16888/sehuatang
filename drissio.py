import random
import time
from typing import Optional, Dict, Any
from DrissionPage import ChromiumOptions, WebPage
from util.log_util import log
from util.config import domain, proxy_enable, proxy_url
from util.read_config import get_config
from util.exceptions import handle_exceptions, ExceptionHandler


class BrowserAutomation:
    """
    浏览器自动化类
    负责管理浏览器实例和页面操作
    """

    def __init__(self, proxy_enable: bool = False, proxy_url: Optional[str] = None):
        """
        初始化浏览器自动化实例

        Args:
            proxy_enable: 是否启用代理
            proxy_url: 代理URL
        """
        self.page_instance: Optional[WebPage] = None
        self.proxy_enable = proxy_enable
        self.proxy_url = proxy_url

        # 从配置文件获取浏览器配置
        self.browser_config = self._load_browser_config()

        # 初始化浏览器页面
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
            "sleep_range": [3, 5],
            "page_load_timeout": 30,
            "cloudflare_timeout": 10,
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
        """初始化浏览器页面实例"""
        if self.page_instance is None:
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

            # 创建WebPage实例
            try:
                self.page_instance = WebPage(chromium_options=co)
                log.info("浏览器页面实例初始化成功")
            except Exception as e:
                log.error(f"初始化WebPage实例失败: {e}")
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

        # 随机等待时间
        sleep_range = self.browser_config["sleep_range"]
        sleep_duration = random.uniform(sleep_range[0], sleep_range[1])
        time.sleep(sleep_duration)

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
            time.sleep(3)

            # 点击验证按钮
            checkbox = frame.ele('.cb-i')
            checkbox.click()

            # 等待页面加载
            self.page_instance.wait.load_start()
            time.sleep(5)

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
                time.sleep(1)
                enter_button.click()
                time.sleep(3)
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
        if self.page_instance is not None:
            try:
                self.page_instance.quit()
                log.debug("浏览器页面已关闭")
            except Exception as e:
                log.error(f"关闭浏览器页面时出错: {e}")
            finally:
                self.page_instance = None

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
    # 使用上下文管理器（推荐）
    with BrowserAutomation(proxy_enable=proxy_enable, proxy_url=proxy_url) as browser:
        url = "https://sehuatang.org"
        html = browser.get_page_html(url)
        print(f"页面标题: {browser.get_page_title()}")
        print(f"当前URL: {browser.get_current_url()}")
        print(f"HTML长度: {len(html)}")

    # 传统用法（仍然支持）
    # browser = BrowserAutomation(proxy_enable=proxy_enable, proxy_url=proxy_url)
    # try:
    #     html = browser.get_page_html("https://sehuatang.org")
    #     print(html)
    # finally:
    #     browser.close_page()
