import time
from DrissionPage import WebPage, ChromiumOptions
from util.config import proxy_url, domain, cookie, proxy_enable
from util.log_util import log

def get_user_agent_and_cookies():

    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    chromium_options = ChromiumOptions()
    if proxy_enable:
        log.debug("proxy enable url is :", proxy_url)
        chromium_options.set_proxy(proxy_url)
    # print("user_agent:", user_agent)
    chromium_options.headless()
    chromium_options.set_user_agent(user_agent)
    page = WebPage(chromium_options=chromium_options)
    page.get(f"http://{domain}")
    if page.title == "Just a moment...":
        print(page.title)
        i = page.get_frame('@src^https://challenges.cloudflare.com/cdn-cgi')
        time.sleep(5)
        e = i('.cb-i')
        time.sleep(5)
        e.click()
        time.sleep(5)
    time.sleep(3)
    user_agent = page._headers.get('User-Agent')
    page_cookies = page.cookies(as_dict=True)
    
    if cookie:
        page.set.cookies(cookie)
        page.refresh()
        page_cookies = page.cookies(as_dict=True)
    log.debug("-*-"*10)
    log.debug(f"page_html: {page.html}")
    log.debug(f"user_agent: {user_agent}")
    log.debug(f"cookies: {page_cookies}")
    log.debug("-*-"*10)
    page.quit()
    return user_agent, page_cookies

if __name__ == '__main__':
    user_agent, page_cookies = get_user_agent_and_cookies()
    print("User-Agent:", user_agent)
    print("Cookies:", page_cookies)




