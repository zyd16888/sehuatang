import time
from DrissionPage import WebPage, ChromiumOptions, SessionPage
from util.config import proxy_url, domain, cookie, proxy_enable, domain_get_url
from util.log_util import log
import httpx
import requests

def get_user_agent_and_cookies():

    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    co = ChromiumOptions()
    if proxy_enable:
        log.debug(f"proxy enable url is : {proxy_url}")
        co.set_proxy(proxy_url)
    # print("user_agent:", user_agent)
    # chromium_options.headless()
    co.set_user_agent(user_agent)
    page = WebPage(chromium_options=co)
    page.get("https://sehuatang.org")
    page.close()
    if page.title == "Just a moment...":
        print(page.title)
        i = page.get_frame('@src^https://challenges.cloudflare.com/cdn-cgi')
        page.wait.eles_loaded('.cb-i')
        e = i.ele('.cb-i')
        e.click()
        page.wait.load_start()
        time.sleep(3)

    if page.title == domain.upper() :
        enterdiv = page.ele('.enter-btn')
        log.debug(enterdiv.html)
        time.sleep(1)
        enterdiv.click()
        time.sleep(3)

    user_agent = page._headers.get('User-Agent')
    page_cookies = page.cookies(as_dict=True)
    page_headers = page._headers
    log.debug("-*-"*10)
    log.debug(f"page_html: {page.html}")
    log.debug(f"page_headers: {page_headers}")
    log.debug(f"cookies: {page_cookies}")
    log.debug("-*-"*10)
    page.cookies_to_session()
    session = page.session
    # 切换到收发数据包模式
    page.change_mode(go=False)
    page.get("https://sehuatang.org/forum-103-3.html")
    # page.wait.load_end()
    print(page.html)
    page.quit()
    return page_headers, page_cookies, session

if __name__ == '__main__':
    page_headers, page_cookies, session = get_user_agent_and_cookies()
    print("page_headers:", page_headers)
    print("Cookies:", page_cookies)

    # with httpx.Client(headers=dict(page_headers), cookies=page_cookies) as client:
    #     response = client.get("https://sehuatang.org/forum-103-2.html")

    response = session.get(url="https://sehuatang.org/forum-103-2.html")
    

    # response = httpx.get(url="https://sehuatang.org/forum-103-2.html", cookies=page_cookies, headers={'User-Agent': user_agent} )

    if response.status_code == 200:
        print('请求成功！')
        print('响应内容：', response.text)
    else:
        print(f'请求失败，状态码：{response.status_code} {response.text}' )




