"""
页面解析器模块
负责解析HTML页面，提取所需数据
"""
import html
import re
from typing import List, Dict, Any, Optional
import bs4
from util.log_util import log


class PageParser:
    """页面解析器类"""
    
    def __init__(self):
        self.log = log

        self._magnet_re = re.compile(
            r"magnet:\?xt=urn:btih:[A-Za-z0-9]{32,40}(?:[A-Za-z0-9&=%._+:/-]*)",
            re.IGNORECASE,
        )
        self._link_115_re = re.compile(
            r"(115://[A-Za-z0-9+/=]+|https?://(?:115\.com|115cdn\.com)/[^\s\"'<>]+)",
            re.IGNORECASE,
        )
    
    def parse_plate_page(self, html_content: str, date_time: str, strict: bool = False) -> tuple[List[Dict[str, Any]], List[str]]:
        """
        解析板块页面，提取帖子信息
        
        Args:
            html_content: HTML页面内容
            date_time: 目标日期，格式: 2019-01-01
            
        Returns:
            tuple: (帖子信息列表, 帖子ID列表)
        """
        all_info = self.parse_plate_page_all(html_content, strict=strict)
        info_list = [
            item for item in all_info
            if str(item.get("date", "")).startswith(date_time)
        ]
        tid_list = [item["tid"] for item in info_list]
        return info_list, tid_list

    def parse_plate_page_all(self, html_content: str, strict: bool = False) -> List[Dict[str, Any]]:
        """解析列表页中的全部普通主题，不做日期过滤。"""
        info_list = []
        try:
            soup = bs4.BeautifulSoup(html_content, "html.parser")
            all_threads = soup.find_all(id=re.compile("^normalthread_"))
            if strict and not all_threads and not soup.find(id="threadlist") and not soup.find(id="threadlisttableid"):
                raise ValueError("响应缺少板块列表结构")
            for thread in all_threads:
                data = self._extract_thread_info(thread)
                if data:
                    info_list.append(data)
            if strict and all_threads and not info_list:
                raise ValueError("板块主题存在但无法解析")
        except Exception as e:
            self.log.error(f"解析板块页面时出错: {e}")
            if strict:
                raise
        return info_list

    def parse_last_page(self, html_content: str) -> int:
        """从 Discuz 分页栏提取最后一页页码。"""
        try:
            soup = bs4.BeautifulSoup(html_content, "html.parser")
            page_numbers = []
            current_page = soup.select_one("div.pg strong")
            if current_page and current_page.get_text(strip=True).isdigit():
                page_numbers.append(int(current_page.get_text(strip=True)))

            for anchor in soup.select("div.pg a[href]"):
                href = anchor.get("href", "")
                matches = re.findall(
                    r"(?:[?&]page=|forum-\d+-)(\d+)",
                    href,
                )
                page_numbers.extend(int(value) for value in matches)

                text_numbers = re.findall(r"\d+", anchor.get_text(" ", strip=True))
                page_numbers.extend(int(value) for value in text_numbers)

            return max(page_numbers, default=1)
        except Exception as e:
            self.log.error(f"解析板块最后页码时出错: {e}")
            return 1
    
    def _extract_thread_info(self, thread_element) -> Optional[Dict[str, Any]]:
        """
        从线程元素中提取信息
        
        Args:
            thread_element: BeautifulSoup元素
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
            date_td = thread_element.find("td", class_="by")
            date_td_em = date_td.find("em") if date_td else None
            if not date_td_em:
                return None

            date_span = date_td_em.find("span", attrs={"title": True})
            if date_span is not None:
                date = date_span.get("title")
            else:
                date = date_td_em.get_text(" ", strip=True)
                    
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
            
            # 获取首帖内容，避免回帖/引用干扰
            info_element = self._get_primary_post_element(soup)
            if not info_element:
                return None
                
            # 提取图片列表
            img_list = []
            for img in info_element.find_all("img"):
                if "file" in img.attrs:
                    img_list.append(img.attrs["file"])
            
            # 提取磁力链接
            magnet = self._extract_magnet_link(info_element)
            magnet_115 = self._extract_115_link(info_element)
            
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
    
    def _get_primary_post_element(self, soup):
        """获取首帖正文元素"""
        try:
            post_list = soup.find("div", id="postlist")
            if post_list:
                info_element = post_list.find("td", class_="t_f")
                if info_element:
                    return info_element
            return soup.find("td", class_="t_f")
        except Exception as e:
            self.log.error(f"获取首帖内容时出错: {e}")
            return None

    def _extract_magnet_link(self, content_root) -> Optional[str]:
        """提取磁力链接"""
        try:
            link_candidates = self._extract_magnets_from_links(content_root)
            code_candidates = self._extract_magnets_from_blockcode(content_root)
            text_candidates = self._extract_magnets_from_text(
                content_root.get_text(" ", strip=True)
            )
            return self._pick_first_candidate(
                [link_candidates, code_candidates, text_candidates]
            )
        except Exception as e:
            self.log.error(f"提取磁力链接时出错: {e}")
        return None
    
    def _extract_115_link(self, content_root) -> Optional[str]:
        """提取115链接"""
        try:
            link_candidates = self._extract_115_from_links(content_root)
            code_candidates = self._extract_115_from_blockcode(content_root)
            text_candidates = self._extract_115_from_text(
                content_root.get_text(" ", strip=True)
            )
            return self._pick_first_candidate(
                [link_candidates, code_candidates, text_candidates]
            )
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

    def _clean_candidate_text(self, text: str) -> str:
        """清理候选文本，去掉转义和HTML标签"""
        if not text:
            return ""
        cleaned = html.unescape(text).strip()
        if "<" in cleaned and ">" in cleaned:
            try:
                cleaned = bs4.BeautifulSoup(cleaned, "html.parser").get_text(
                    " ", strip=True
                )
            except Exception:
                pass
        return cleaned.strip()

    def _strip_trailing_punct(self, text: str) -> str:
        return text.rstrip(").,，。;；:!?]}>\"'")

    def _extract_magnets_from_links(self, content_root) -> List[str]:
        magnets = []
        for anchor in content_root.find_all("a", href=True):
            href = anchor["href"].strip()
            if href.lower().startswith("magnet:?"):
                magnets.extend(self._extract_magnets_from_text(href))
        return magnets

    def _extract_magnets_from_blockcode(self, content_root) -> List[str]:
        magnets = []
        for node in content_root.select("div.blockcode li, div.blockcode code"):
            magnets.extend(
                self._extract_magnets_from_text(node.get_text(" ", strip=True))
            )
        return magnets

    def _extract_magnets_from_text(self, text: str) -> List[str]:
        cleaned = self._clean_candidate_text(text)
        if not cleaned:
            return []
        matches = self._magnet_re.findall(cleaned)
        return [self._strip_trailing_punct(m) for m in matches]

    def _extract_115_from_links(self, content_root) -> List[str]:
        links = []
        for anchor in content_root.find_all("a", href=True):
            href = anchor["href"].strip()
            if href.lower().startswith("115://") or "115.com" in href:
                links.extend(self._extract_115_from_text(href))
        return links

    def _extract_115_from_blockcode(self, content_root) -> List[str]:
        links = []
        for node in content_root.select("div.blockcode li, div.blockcode code"):
            links.extend(
                self._extract_115_from_text(node.get_text(" ", strip=True))
            )
        return links

    def _extract_115_from_text(self, text: str) -> List[str]:
        cleaned = self._clean_candidate_text(text)
        if not cleaned:
            return []
        matches = self._link_115_re.findall(cleaned)
        return [self._strip_trailing_punct(m) for m in matches]

    def _pick_first_candidate(self, candidate_groups: List[List[str]]) -> Optional[str]:
        seen = set()
        for group in candidate_groups:
            for item in group:
                if not item or item in seen:
                    continue
                seen.add(item)
                return item
        return None
