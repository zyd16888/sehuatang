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
        self.initialize_page()

    def initialize_page(self):
        if self.page_instance is None:
            co = ChromiumOptions()
            if self.proxy_enable:
                log.debug(f"proxy enable url is : {self.proxy_url}")
                co.set_proxy(self.proxy_url)
            co.set_user_agent(self.user_agent)
            # co.headless()
            self.page_instance = WebPage(chromium_options=co)

    def get_page_html(self, url):
        self.page_instance.get(url)
        log.debug(f"Browser page url is : {url}")
        if self.page_instance.title == "Just a moment...":
            log.debug(self.page_instance.title)
            log.debug("触发cloudflare challenge验证")
            i = self.page_instance.get_frame('@src^https://challenges.cloudflare.com/cdn-cgi')
            self.page_instance.wait.eles_loaded('.cb-i')
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
    html = browser.get_page_html(url, domain)
    print(html)
    browser.close_page()
