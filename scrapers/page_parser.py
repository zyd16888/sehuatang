"""
页面解析器模块
负责解析HTML页面，提取所需数据
"""
import re
from typing import List, Dict, Any, Optional
import bs4
from util.log_util import log


class PageParser:
    """页面解析器类"""
    
    def __init__(self):
        self.log = log
    
    def parse_plate_page(self, html_content: str, date_time: str) -> tuple[List[Dict[str, Any]], List[str]]:
        """
        解析板块页面，提取帖子信息
        
        Args:
            html_content: HTML页面内容
            date_time: 目标日期，格式: 2019-01-01
            
        Returns:
            tuple: (帖子信息列表, 帖子ID列表)
        """
        info_list = []
        tid_list = []
        
        try:
            soup = bs4.BeautifulSoup(html_content, "html.parser")
            all_threads = soup.find_all(id=re.compile("^normalthread_"))
            
            for thread in all_threads:
                data = self._extract_thread_info(thread, date_time)
                if data:
                    info_list.append(data)
                    tid_list.append(data["tid"])
                    
        except Exception as e:
            self.log.error(f"解析板块页面时出错: {e}")
            
        return info_list, tid_list
    
    def _extract_thread_info(self, thread_element, date_time: str) -> Optional[Dict[str, Any]]:
        """
        从线程元素中提取信息
        
        Args:
            thread_element: BeautifulSoup元素
            date_time: 目标日期
            
        Returns:
            帖子信息字典或None
        """
        try:
            # 提取标题和编号
            title_element = thread_element.find("a", class_="s xst")
            if not title_element:
                return None
                
            title_list = title_element.get_text().split(" ")
            number = title_list[0]
            title_list.pop(0)
            title = " ".join(title_list)
            
            # 提取日期
            date_td_em = thread_element.find("td", class_="by").find("em")
            date_span = date_td_em.find("span", attrs={"title": re.compile("^" + date_time)})
            
            if date_span is not None:
                date = date_span.attrs["title"]
            else:
                flag = date_td_em.get_text().startswith(date_time)
                if flag:
                    date = date_td_em.get_text()
                else:
                    return None
                    
            if date is None:
                return None
                
            # 提取帖子ID
            tid_element = thread_element.find(class_="showcontent y")
            if not tid_element:
                return None
                
            tid = tid_element.attrs["id"].split("_")[1]
            
            return {
                "number": number,
                "title": title,
                "date": date,
                "tid": tid
            }
            
        except Exception as e:
            self.log.error(f"提取线程信息时出错: {e}")
            return None
    
    def parse_thread_page(self, html_content: str) -> Optional[Dict[str, Any]]:
        """
        解析帖子页面，提取详细信息
        
        Args:
            html_content: HTML页面内容
            
        Returns:
            帖子详细信息字典或None
        """
        try:
            soup = bs4.BeautifulSoup(html_content, "html.parser")
            
            # 获取帖子标题
            title_element = soup.find("h1", class_="ts")
            if not title_element or not title_element.find("span"):
                return None
            title = title_element.find("span").get_text()
            
            # 获取帖子内容
            info_element = soup.find("td", class_="t_f")
            if not info_element:
                return None
                
            # 提取图片列表
            img_list = []
            for img in info_element.find_all("img"):
                if "file" in img.attrs:
                    img_list.append(img.attrs["file"])
            
            # 提取磁力链接
            magnet = self._extract_magnet_link(soup)
            magnet_115 = self._extract_115_link(soup)
            
            # 提取发布时间
            post_time = self._extract_post_time(soup)
            
            return {
                "title": title,
                "post_time": post_time,
                "img": img_list,
                "magnet": magnet,
                "magnet_115": magnet_115
            }
            
        except Exception as e:
            self.log.error(f"解析帖子页面时出错: {e}")
            return None
    
    def _extract_magnet_link(self, soup) -> Optional[str]:
        """提取磁力链接"""
        try:
            blockcode = soup.find("div", class_="blockcode")
            if blockcode and blockcode.find("li"):
                return blockcode.find("li").get_text()
        except Exception as e:
            self.log.error(f"提取磁力链接时出错: {e}")
        return None
    
    def _extract_115_link(self, soup) -> Optional[str]:
        """提取115链接"""
        try:
            blockcode = soup.find("div", class_="blockcode")
            if blockcode:
                next_blockcode = blockcode.find_next("div", class_="blockcode")
                if next_blockcode and next_blockcode.find("li"):
                    return next_blockcode.find("li").get_text()
        except Exception as e:
            self.log.error(f"提取115链接时出错: {e}")
        return None
    
    def _extract_post_time(self, soup) -> Optional[str]:
        """提取发布时间"""
        try:
            post_time_em = soup.find("img", class_="authicn vm").parent.find("em")
            post_time_span = post_time_em.find("span")
            
            if post_time_span is not None:
                return post_time_span.attrs["title"]
            else:
                return post_time_em.get_text()[4:]
                
        except Exception as e:
            self.log.error(f"提取发布时间时出错: {e}")
            return None
