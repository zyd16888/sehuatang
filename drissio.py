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
            # Add headless and other recommended startup arguments
            co.set_argument("--headless=new")
            co.set_argument("--no-sandbox")
            co.set_argument("--disable-dev-shm-usage")

            # Additional debug log
            log.debug(f"Chromium options configured: {co.arguments}")

            # Attempt to create a WebPage instance
            try:
                self.page_instance = WebPage(chromium_options=co)
                log.info("Browser page instance successfully initialized.")
            except Exception as e:
                log.error(f"Error initializing WebPage: {e}")
                raise

    def get_page_html(self, url):
        self.page_instance.get(url)
        log.debug(f"Browser page url is : {url}")
        # 选择3到5秒之间的随机暂停时间
        sleep_duration = random.uniform(3, 5)
        time.sleep(sleep_duration)
        if self.page_instance.title == "Just a moment...":
            log.debug(self.page_instance.title)
            log.debug("触发cloudflare challenge验证")
            i = self.page_instance.get_frame('@src^https://challenges.cloudflare.com/cdn-cgi')
            self.page_instance.wait.eles_loaded('.cb-i')
            time.sleep(3)
            print(self.page_instance.html)
            e = i.ele('.cb-i')
            e.click()
            self.page_instance.wait.load_start()
            time.sleep(5)

        if self.page_instance.title == domain.upper():
            enterdiv = self.page_instance.ele('.enter-btn')
            log.debug(enterdiv.html)
            time.sleep(1)
            enterdiv.click()
            time.sleep(3)

        page_html = self.page_instance.html
        log.debug(f"Browser page title is : {self.page_instance.title}")
        return page_html

    def close_page(self):
        if self.page_instance is not None:
            self.page_instance.quit()
            self.page_instance = None

# 示例用法
if __name__ == "__main__":
    browser = BrowserAutomation(proxy_enable=proxy_enable, proxy_url=proxy_url)
    url = "https://sehuatang.org"
    html = browser.get_page_html(url)
    print(html)
    browser.close_page()
