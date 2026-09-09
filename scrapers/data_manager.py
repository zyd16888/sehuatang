"""
数据管理器模块
统一管理数据库操作（MongoDB）
"""
from typing import List, Dict, Any, Tuple
from util.log_util import log
from util.config import mongodb_enable
from util.mongo import save_data, compare_tid, filter_data
from pymongo.errors import BulkWriteError


class DataManager:
    """数据管理器类"""

    def __init__(self):
        self.log = log
        self.mongodb_enable = mongodb_enable

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

        self.log.info("未启用数据库，返回所有数据")
        return tid_list, info_list

    def filter_and_save_data(
        self,
        data_list: List[Dict[str, Any]],
        fid: int,
        strict: bool = False,
        dry_run: bool = False,
        stats: Dict[str, Any] = None,
    ) -> List[Dict[str, Any]]:
        """
        过滤并保存数据

        Args:
            data_list: 数据列表
            fid: 板块ID

        Returns:
            过滤后的数据列表
        """
        progress = stats if stats is not None else {}
        progress.update(saved=0, existing=0, candidates=len(data_list), saved_records=[], candidate_records=data_list)
        if not self.mongodb_enable:
            self.log.info("未启用数据库，跳过数据保存")
            filtered_data = data_list
        else:
            filtered_data = []
            try:
                filtered_data = filter_data(data_list, fid)
                progress["existing"] = len(data_list) - len(filtered_data)
                progress["candidates"] = len(filtered_data)
                progress["candidate_records"] = filtered_data
                if not dry_run:
                    try:
                        inserted = save_data(filtered_data, fid)
                        if isinstance(inserted, list):
                            progress["existing"] += len(filtered_data) - len(inserted)
                            filtered_data = inserted
                    except BulkWriteError as exc:
                        # ordered 写入的错误前缀已完成；只把 upserted 的记录计为新增。
                        details = exc.details or {}
                        if not details.get("writeConcernErrors"):
                            if "nUpserted" in details:
                                saved_records = [filtered_data[row["index"]] for row in details.get("upserted", [])]
                                boundary = min((row["index"] for row in details.get("writeErrors", [])),
                                               default=len(filtered_data))
                                progress["completed_records"] = filtered_data[:boundary]
                                progress["confirmed_existing"] = max(0, boundary - len(saved_records))
                                progress["existing"] += progress["confirmed_existing"]
                            else:
                                saved_records = filtered_data[:min(len(filtered_data), int(details.get("nInserted", 0)))]
                            progress["saved"] = len(saved_records)
                            progress["saved_records"] = saved_records
                        raise
                    progress["saved"] = len(filtered_data)
                    progress["saved_records"] = filtered_data
                    self.log.info(f"MongoDB保存数据成功，共{len(filtered_data)}条")
            except Exception as e:
                self.log.error(f"MongoDB操作失败: {e}")
                if strict:
                    raise

        if dry_run:
            self.log.info(f"dry-run：跳过数据写入，共{len(filtered_data)}条")

        return filtered_data
