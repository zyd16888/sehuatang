"""
数据处理器模块
负责数据的过滤、转换和验证
"""
import re
from typing import List, Dict, Any, Optional
from util.log_util import log
from util.config import date


class DataProcessor:
    """数据处理器类"""

    def __init__(
        self,
        target_date: Optional[str] = None,
        date_filter: bool = True,
    ):
        self.log = log
        self.target_date = target_date
        # 分页补抓不限定日期，置 False 跳过发布时间匹配
        self.date_filter = date_filter

    def merge_thread_data(self, thread_details: List[tuple], _: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        合并线程详细信息和基本信息
        
        Args:
            thread_details: 线程详细信息列表 [(详细信息, 基本信息), ...]
            thread_infos: 线程基本信息列表
            
        Returns:
            合并后的数据列表
        """
        merged_data = []

        # 过滤掉None结果
        valid_results = [
            result for result in thread_details if result is not None]

        for detail_data, basic_info in valid_results:
            if detail_data is None:
                continue

            # 合并数据
            merged_item = {
                **detail_data,
                "number": basic_info["number"],
                "title": basic_info["title"],
                "date": basic_info["date"],
                "tid": basic_info["tid"]
            }

            # 验证发布时间是否匹配目标日期
            if not merged_item.get("post_time") or not self.date_filter or self._is_valid_post_time(
                merged_item.get("post_time", "")
            ):
                merged_data.append(merged_item)
            else:
                self.log.debug(f"帖子 {merged_item.get('tid')} 发布时间不匹配，跳过")

        return merged_data

    def _is_valid_post_time(self, post_time: str) -> bool:
        """
        验证发布时间是否匹配目标日期
        
        Args:
            post_time: 发布时间字符串
            
        Returns:
            是否匹配
        """
        try:
            target_date = self.target_date or date()
            return bool(re.match("^" + target_date, post_time))
        except Exception as e:
            self.log.error(f"验证发布时间时出错: {e}")
            return False

    def validate_data(self, data_item: Dict[str, Any]) -> bool:
        """
        验证数据项的完整性
        
        Args:
            data_item: 数据项
            
        Returns:
            是否有效
        """
        required_fields = ["tid", "post_time", "magnet"]

        for field in required_fields:
            if field not in data_item or not data_item[field]:
                self.log.warning(f"数据项缺少必要字段 {field}")
                self.log.warning(f"无效数据项: {data_item}")
                return False

        return True

    def clean_data(self, data_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        清理数据，移除无效项
        
        Args:
            data_list: 原始数据列表
            
        Returns:
            清理后的数据列表
        """
        cleaned_data = []

        for item in data_list:
            if self.validate_data(item):
                # 清理字符串字段
                cleaned_item = self._clean_item(item)
                cleaned_data.append(cleaned_item)
            else:
                self.log.warning(f"跳过无效数据项: {item.get('tid', 'unknown')}")

        return cleaned_data

    def _clean_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        清理单个数据项
        
        Args:
            item: 数据项
            
        Returns:
            清理后的数据项
        """
        cleaned_item = item.copy()

        # 清理字符串字段的空白字符
        string_fields = ["title", "number", "magnet", "magnet_115"]
        for field in string_fields:
            if field in cleaned_item and isinstance(cleaned_item[field], str):
                cleaned_item[field] = cleaned_item[field].strip()

        # 确保图片列表不为空
        if "img" not in cleaned_item or not cleaned_item["img"]:
            cleaned_item["img"] = []

        return cleaned_item
