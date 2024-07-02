import asyncio
import bs4
import re
import time

from drissio import BrowserAutomation
from util.mongo import save_data, compare_tid, filter_data
from util.log_util import log
from util.save_to_mysql import SaveToMysql
from util.sendTelegram import send_media_group, rec_message
from util.config import (
    domain,
    fid_list,
    page_num,
    date,
    mongodb_enable,
    mysql_enable,
    tg_enable,
    proxy,
    proxy_enable
)

user_agent = ""
page_cookies = {}

browser = BrowserAutomation(proxy_enable=proxy_enable, proxy_url=proxy)

# 获取帖子的id(访问板块)
async def get_plate_info(fid: int, page: int, proxy: str, date_time):
    """
    :param fid: 板块id
    :param page: 页码
    :param proxy: 代理服务器地址
    :param date_time: 日期，格式: 2019-01-01

    :return: info_list
    """
    log.info("Crawl the plate " + str(fid) + " page number " + str(page))
    url = "https://{}/forum-{}-{}.html".format(domain, fid, page)
    # headers
    # headers = {
    #     "User-Agent": user_agent
    # }

    # 存放字典的列表
    info_list = []
    tid_list = []

    # log.debug(f"get_plate_info url is : {url}, headers is : {headers}, cookies is : {page_cookies}")

    # async with httpx.AsyncClient(proxies=proxy) as client:
    #     response = await client.get(url, headers=headers, cookies=page_cookies)

    html_response = browser.get_page_html(url)

    # log.debug(f"get_plate_info response is : {html_response}")
    # 使用bs4解析
    soup = bs4.BeautifulSoup(html_response, "html.parser")
    # print(soup)
    all = soup.find_all(id=re.compile("^normalthread_"))
    try:
        for i in all:
            data = {}
            title_list = i.find("a", class_="s xst").get_text().split(" ")
            number = title_list[0]
            title_list.pop(0)
            title = " ".join(title_list)
            # date = i.find("span", attrs={"title": re.compile("^" + date_time)})
            date_td_em = i.find("td", class_="by").find("em")
            date_span = date_td_em.find(
                "span", attrs={"title": re.compile("^" + date_time)}
            )
            if date_span is not None:
                date = date_span.attrs["title"]
            else:
                flag = date_td_em.get_text().startswith(date_time)
                if flag:
                    date = date_td_em.get_text()
                else:
                    continue
            if date is None:
                continue
            id = i.find(class_="showcontent y").attrs["id"].split("_")[1]
            data["number"] = number
            data["title"] = title
            data["date"] = date
            data["tid"] = id
            info_list.append(data)
            tid_list.append(id)
        log.debug("Crawl the plate " + str(fid) + " page number " + str(page))
        log.debug(" ".join(tid_list))
    except Exception as e:
        log.error(e)
    return info_list, tid_list


# 访问每个帖子的页面
async def get_page(tid, proxy, f_info):
    """
    :param tid: 帖子id
    :param proxy: 代理服务器地址
    :param f_info: 帖子信息
    """

    data = {}
    url = "https://{}/?mod=viewthread&tid={}".format(domain, tid)
    # headers
    headers = {
        "User-Agent": user_agent
    }

    try:
        # log.debug(f"get_page url is : {url}, headers is : {headers}, cookies is : {page_cookies}")
        # async with httpx.AsyncClient(proxies=proxy) as client:
        #     response = await client.get(url, headers=headers, cookies=page_cookies)

        html_response = browser.get_page_html(url)

        soup = bs4.BeautifulSoup(html_response, "html.parser")
        # 获取帖子的标题
        title = soup.find("h1", class_="ts").find("span").get_text()
        # 楼主发布的内容
        info = soup.find("td", class_="t_f")
        # 存放图片的列表
        img_list = []
        for i in info.find_all("img"):
            img_list.append(i.attrs["file"])
        # 磁力链接
        magnet = soup.find("div", class_="blockcode").find("li").get_text()
        # 查找下一个blockcode
        next_blockcode = soup.find("div", class_="blockcode").find_next(
            "div", class_="blockcode"
        )
        if next_blockcode is not None:
            magnet_115 = next_blockcode.find("li").get_text()
        else:
            magnet_115 = None

        post_time_em = soup.find("img", class_="authicn vm").parent.find("em")
        post_time_span = post_time_em.find("span")
        if post_time_span is not None:
            post_time = post_time_span.attrs["title"]
        else:
            post_time = post_time_em.get_text()[4:]

        data["title"] = title
        data["post_time"] = post_time
        data["img"] = img_list
        data["magnet"] = magnet
        data["magnet_115"] = magnet_115
        log.debug("Crawl the page " + tid)
        log.debug(data.values())
        return data, f_info
    except Exception as e:
        log.error("Crawl the page " + tid + " failed.")
        log.error(e)


async def crawler(fid):

    start_time = time.time()
    tasks = [
        get_plate_info(fid, page, proxy, date()) for page in range(1, page_num + 1)
    ]
    # 开始执行协程
    results = await asyncio.gather(*tasks)
    end_time = time.time()
    log.info("get_plate_info 执行时间：" + str(end_time - start_time))

    # 将结果拼接
    info_list_all = []
    tid_list_all = []
    for result in results:
        info_list, tid_list = result
        info_list_all.extend(info_list)
        tid_list_all.extend(tid_list)
    log.info("即将开始爬取的页面 " + " ".join(tid_list_all))
    if mongodb_enable:
        log.info("mongodb_enable is True")
        tid_list_new, info_list_new = compare_tid(tid_list_all, fid, info_list_all)
    elif mysql_enable:
        mysql = SaveToMysql()
        tid_list_new, info_list_new = mysql.compare_tid(
            tid_list_all, fid, info_list_all
        )
        mysql.close()
    else:
        tid_list_new = tid_list_all
        info_list_new = info_list_all
    log.info("需要爬取的页面 " + " ".join(tid_list_new))

    data_list = []
    start_time = time.time()
    tasks = [get_page(i["tid"], proxy, i) for i in info_list_new]
    results = await asyncio.gather(*tasks)
    end_time = time.time()
    log.info("get_page 执行时间：" + str(end_time - start_time))
    results_new = [i for i in results if i is not None]
    for result in results_new:
        data, i = result
        data["number"] = i["number"]
        data["title"] = i["title"]
        data["date"] = i["date"]
        data["tid"] = i["tid"]
        post_time = data["post_time"]
        # 再次匹配发布时间（因为上级页面获取的时间可能不准确）
        if re.match("^" + date(), post_time):
            data_list.append(data)
    log.info("本次抓取的数据条数为：" + str(len(data_list)))
    log.info("开始写入数据库")
    if mysql_enable:
        mysql = SaveToMysql()
        data_list_new = mysql.filter_data(data_list, fid)
        mysql.save_data(data_list_new, fid)
        mysql.close()
    if mongodb_enable:
        data_list_new = filter_data(data_list, fid)
        save_data(data_list_new, fid)
    if tg_enable:
        send_media_group(data_list, fid)
    if len(data_list) > 0:
        return rec_message(data_list, fid)
    else:
        return "没有新的数据"


async def main():
    log.debug(f"日期: {date()}")

    log.debug(f"浏览主页： {domain}")
    html_response = browser.get_page_html("https://{}".format(domain))
    # 打印页面标题
    soup = bs4.BeautifulSoup(html_response, "html.parser")
    log.debug(soup.title.get_text())

    for fid in fid_list:
        await crawler(fid)

    browser.close_page()


if __name__ == "__main__":
    asyncio.run(main())
