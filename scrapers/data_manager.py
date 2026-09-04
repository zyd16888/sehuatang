"""
数据管理器模块
统一管理数据库操作，包括MongoDB和MySQL
"""
from typing import List, Dict, Any, Tuple
from util.log_util import log
from util.config import mongodb_enable, mysql_enable
from util.mongo import save_data, compare_tid, filter_data
from util.save_to_mysql import SaveToMysql


class DataManager:
    """数据管理器类"""
    
    def __init__(self):
        self.log = log
        self.mongodb_enable = mongodb_enable
        self.mysql_enable = mysql_enable
        self._mysql_instance = None
    
    def compare_existing_data(self, tid_list: List[str], fid: int, info_list: List[Dict[str, Any]]) -> Tuple[List[str], List[Dict[str, Any]]]:
        """
        比较现有数据，过滤出需要抓取的新数据
        
        Args:
            tid_list: 帖子ID列表
            fid: 板块ID
            info_list: 帖子信息列表
            
        Returns:
            tuple: (新的帖子ID列表, 新的帖子信息列表)
        """
        if self.mongodb_enable:
            self.log.info("使用MongoDB进行数据比较")
            return compare_tid(tid_list, fid, info_list)
        elif self.mysql_enable:
            self.log.info("使用MySQL进行数据比较")
            mysql = self._get_mysql_instance()
            try:
                return mysql.compare_tid(tid_list, fid, info_list)
            finally:
                self._close_mysql_instance()
        else:
            self.log.info("未启用数据库，返回所有数据")
            return tid_list, info_list
    
    def filter_and_save_data(
        self,
        data_list: List[Dict[str, Any]],
        fid: int,
        strict: bool = False,
        dry_run: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        过滤并保存数据
        
        Args:
            data_list: 数据列表
            fid: 板块ID
            
        Returns:
            过滤后的数据列表
        """
        filtered_data = []
        
        if self.mysql_enable:
            mysql = self._get_mysql_instance()
            try:
                filtered_data = mysql.filter_data(data_list, fid)
                if not dry_run:
                    mysql.save_data(filtered_data, fid)
                    self.log.info(f"MySQL保存数据成功，共{len(filtered_data)}条")
            except Exception as e:
                self.log.error(f"MySQL操作失败: {e}")
                if strict:
                    raise
            finally:
                self._close_mysql_instance()
        
        if self.mongodb_enable:
            try:
                filtered_data = filter_data(data_list, fid)
                if not dry_run:
                    save_data(filtered_data, fid)
                    self.log.info(f"MongoDB保存数据成功，共{len(filtered_data)}条")
            except Exception as e:
                self.log.error(f"MongoDB操作失败: {e}")
                if strict:
                    raise
        
        if not self.mysql_enable and not self.mongodb_enable:
            self.log.info("未启用数据库，跳过数据保存")
            filtered_data = data_list

        if dry_run:
            self.log.info(f"dry-run：跳过数据写入，共{len(filtered_data)}条")
            
        return filtered_data
    
    def _get_mysql_instance(self) -> SaveToMysql:
        """获取MySQL实例"""
        if self._mysql_instance is None:
            self._mysql_instance = SaveToMysql()
        return self._mysql_instance
    
    def _close_mysql_instance(self):
        """关闭MySQL实例"""
        if self._mysql_instance is not None:
            try:
                self._mysql_instance.close()
            except Exception as e:
                self.log.error(f"关闭MySQL连接时出错: {e}")
            finally:
                self._mysql_instance = None
    
    def __del__(self):
        """析构函数，确保资源被正确释放"""
        self._close_mysql_instance()
