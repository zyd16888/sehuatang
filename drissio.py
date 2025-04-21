import random
from DrissionPage import ChromiumOptions, WebPage
from util.log_util import log
from util.config import domain, proxy_enable, proxy_url
import time

class BrowserAutomation:
    def __init__(self, proxy_enable=False, proxy_url=None):
        self.page_instance = None
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        self.proxy_enable = proxy_enable
        self.proxy_url = proxy_url
        # Initialize the browser page
        try:
            self.initialize_page()
        except Exception as e:
            log.error(f"Failed to initialize browser page: {e}")
            raise

    def initialize_page(self):
        if self.page_instance is None:
            co = ChromiumOptions()
            # Set proxy if enabled
            if self.proxy_enable and self.proxy_url:
                log.debug(f"Enabling proxy with URL: {self.proxy_url}")
                co.set_proxy(self.proxy_url)
            co.set_user_agent(self.user_agent)
            # 添加Docker环境下必需的参数
            co.set_argument("--headless=new")  # 无头浏览器
            co.set_argument("--no-sandbox")  # Docker中必须添加此参数
            co.set_argument("--disable-dev-shm-usage")  # 禁用/dev/shm使用
            co.set_argument("--disable-gpu")  # 禁用GPU加速
            co.set_argument("--disable-extensions")  # 禁用扩展
            co.set_argument("--disable-setuid-sandbox")  # 禁用setuid沙盒
            # 添加更多稳定性参数
            co.set_argument("--remote-debugging-port=9222")
            co.set_argument("--disable-web-security")
            co.set_argument("--ignore-certificate-errors")

            # Additional debug log
            log.debug(f"Chromium options configured: {co.arguments}")

            # Attempt to create a WebPage instance
            try:
                self.page_instance = WebPage(chromium_options=co)
                log.info("Browser page instance successfully initialized.")
            except Exception as e:
                log.error(f"Error initializing WebPage: {e}")
                raise

    def get_page_html(self, url, max_retries=3):
        """获取页面HTML内容，添加重试机制"""
        retry_count = 0
        while retry_count < max_retries:
            try:
                # 如果页面实例不存在，重新初始化
                if self.page_instance is None:
                    self.initialize_page()

                self.page_instance.get(url)
                log.debug(f"Browser page url is : {url}")

                # 选择3到5秒之间的随机暂停时间
                sleep_duration = random.uniform(3, 5)
                time.sleep(sleep_duration)

                # 使用安全的方式获取标题
                try:
                    page_title = self.page_instance.title
                    log.debug(f"页面标题: {page_title}")

                    if page_title == "Just a moment...":
                        log.debug("触发cloudflare challenge验证")
                        try:
                            i = self.page_instance.get_frame(
                                '@src^https://challenges.cloudflare.com/cdn-cgi')
                            self.page_instance.wait.eles_loaded(
                                '.cb-i', timeout=10)
                            time.sleep(3)

                            e = i.ele('.cb-i')
                            e.click()
                            self.page_instance.wait.load_start()
                            time.sleep(5)
                        except Exception as e:
                            log.error(f"处理Cloudflare验证时出错: {e}")

                    if page_title == domain.upper():
                        try:
                            enterdiv = self.page_instance.ele('.enter-btn')
                            log.debug(
                                f"找到入口按钮: {enterdiv.html if enterdiv else 'None'}")
                            time.sleep(1)
                            enterdiv.click()
                            time.sleep(3)
                        except Exception as e:
                            log.error(f"点击入口按钮时出错: {e}")
                except Exception as e:
                    log.error(f"获取页面标题时出错: {e}")

                # 获取页面HTML
                try:
                    page_html = self.page_instance.html
                    log.debug(f"成功获取页面HTML，页面标题: {self.page_instance.title}")
                    return page_html
                except Exception as e:
                    log.error(f"获取页面HTML时出错: {e}")
                    raise

            except Exception as e:
                retry_count += 1
                log.error(f"获取页面时出错 (重试 {retry_count}/{max_retries}): {e}")

                # 关闭并重新初始化浏览器
                self.close_page()
                time.sleep(2)  # 等待浏览器彻底关闭

                if retry_count >= max_retries:
                    log.error(f"达到最大重试次数 ({max_retries})，返回空HTML")
                    return ""

        return ""

    def close_page(self):
        """安全关闭页面实例"""
        if self.page_instance is not None:
            try:
                self.page_instance.quit()
                log.debug("浏览器页面已关闭")
            except Exception as e:
                log.error(f"关闭浏览器页面时出错: {e}")
            finally:
                self.page_instance = None

# 示例用法
if __name__ == "__main__":
    browser = BrowserAutomation(proxy_enable=proxy_enable, proxy_url=proxy_url)
    url = "https://sehuatang.org"
    html = browser.get_page_html(url)
    print(html)
    browser.close_page()
