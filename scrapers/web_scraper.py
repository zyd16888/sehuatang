"""
Web 爬虫核心模块（HTTP 版）
- 抓取层走 curl_cffi（见 scrapers/http_client.py），不再依赖浏览器
- 列表页 / 详情页用 ThreadPoolExecutor 并发拉取
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as calendar_date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from util.log_util import log
from util.config import date, domain, mongodb_enable, page_num
from util.read_config import get_config

from .data_manager import DataManager
from .data_processor import DataProcessor
from .http_client import HttpClient
from .notification_manager import NotificationManager
from .page_parser import PageParser
from .core.contracts import CrawlFailure
from .infrastructure import build_failure_store


class WebScraper:
    """Web 爬虫主类"""

    def __init__(
        self,
        target_date: Optional[str] = None,
        dry_run: bool = False,
        failure_store=None,
    ):
        self.log = log
        self.http = HttpClient()
        self.workers = self.http.settings.concurrency
        self.target_date = target_date
        self.dry_run = dry_run
        self.failure_store = failure_store or build_failure_store(
            mongodb_enabled=bool(mongodb_enable)
        )

        self.page_parser = PageParser()
        self.data_processor = DataProcessor(target_date)
        self.data_manager = DataManager()
        self.notification_manager = NotificationManager()

    # ---------- 对外入口 ----------

    async def crawl_forum_section(self, fid: int) -> str:
        self.log.info(f"开始爬取板块 {fid}")
        try:
            plate_info_list, tid_list = self._get_plate_info_batch(fid)
            if not tid_list:
                self.log.info(f"板块 {fid} 没有找到符合条件的帖子")
                return "没有新的数据"

            self.log.info(f"即将开始爬取的页面: {' '.join(tid_list)}")

            new_tid_list, new_info_list = self.data_manager.compare_existing_data(
                tid_list, fid, plate_info_list
            )
            if not new_tid_list:
                self.log.info(f"板块 {fid} 没有新数据需要爬取")
                return "没有新的数据"

            self.log.info(f"需要爬取的页面: {' '.join(new_tid_list)}")

            detailed_data_list = self._get_thread_details_batch(new_info_list)
            if not detailed_data_list:
                self.log.info(f"板块 {fid} 没有获取到有效的详细数据")
                return "没有新的数据"

            self.log.info(f"本次抓取的数据条数为: {len(detailed_data_list)}")

            self.log.info("开始写入数据库")
            filtered_data = self.data_manager.filter_and_save_data(
                detailed_data_list,
                fid,
                strict=True,
                dry_run=self.dry_run,
            )
            if not self.dry_run:
                self._clear_detail_failures(detailed_data_list)

            if self.dry_run:
                return f"dry-run：发现 {len(filtered_data)} 条新数据"
            return self.notification_manager.send_notifications(filtered_data, fid)
        except Exception as e:
            self.log.error(f"爬取板块 {fid} 时出错: {e}")
            return f"爬取失败: {str(e)}"

    async def backfill_forum_section(
        self,
        fid: int,
        year: int,
        resume: bool = False,
    ) -> Dict[str, int]:
        """按年份定位板块页码，并分批补抓历史主题。"""
        target_year = str(year)
        self.target_date = target_year
        self.data_processor.target_date = target_year

        page_range = self._locate_year_page_range(fid, year)
        if not page_range:
            self.log.info(f"板块 {fid} 没有定位到 {year} 年的数据页")
            return {
                "page_start": 0,
                "page_end": 0,
                "pages_scanned": 0,
                "threads_found": 0,
                "details_requested": 0,
                "detail_failures": 0,
                "records_saved": 0,
            }

        start_page, end_page = page_range
        located_start_page = start_page
        self.log.info(
            f"板块 {fid} 的 {year} 年数据位于第 {start_page}-{end_page} 页"
        )
        if resume:
            completed_page = self._load_backfill_checkpoint(year, fid)
            start_page = max(start_page, completed_page + 1)
            if completed_page:
                self.log.info(
                    f"板块 {fid} 从检查点第 {completed_page + 1} 页继续"
                )

        summary = {
            "page_start": located_start_page,
            "page_end": end_page,
            "pages_scanned": 0,
            "threads_found": 0,
            "details_requested": 0,
            "detail_failures": 0,
            "records_saved": 0,
        }
        if start_page > end_page:
            self.log.info(f"板块 {fid} 的 {year} 年历史补抓已完成，无需继续")
            return summary

        batch_size = max(1, self.workers)
        checkpoint_enabled = not self.dry_run

        for batch_start in range(start_page, end_page + 1, batch_size):
            pages = list(
                range(batch_start, min(batch_start + batch_size, end_page + 1))
            )
            info_list, tid_list = self._get_plate_info_pages(
                fid,
                pages,
                target_year,
                ordered_by_dateline=True,
                fail_on_missing=True,
            )
            summary["pages_scanned"] += len(pages)
            summary["threads_found"] += len(tid_list)

            if not tid_list:
                if checkpoint_enabled:
                    self._save_backfill_checkpoint(year, fid, pages[-1])
                continue

            new_tid_list, new_info_list = self.data_manager.compare_existing_data(
                tid_list,
                fid,
                info_list,
            )
            summary["details_requested"] += len(new_tid_list)
            if not new_info_list:
                if checkpoint_enabled:
                    self._save_backfill_checkpoint(year, fid, pages[-1])
                continue

            detailed_data, failure_count = self._get_thread_details_batch_result(
                new_info_list
            )
            summary["detail_failures"] += failure_count
            if failure_count:
                checkpoint_enabled = False
                self.log.warning(
                    f"板块 {fid} 第 {pages[0]}-{pages[-1]} 页有 "
                    f"{failure_count} 个详情页失败，检查点暂停推进"
                )

            if not detailed_data:
                if checkpoint_enabled:
                    self._save_backfill_checkpoint(year, fid, pages[-1])
                continue

            saved_data = self.data_manager.filter_and_save_data(
                detailed_data,
                fid,
                strict=True,
                dry_run=self.dry_run,
            )
            summary["records_saved"] += len(saved_data)
            if not self.dry_run:
                self._clear_detail_failures(detailed_data)
            if checkpoint_enabled:
                self._save_backfill_checkpoint(year, fid, pages[-1])
            self.log.info(
                f"板块 {fid} 历史补抓进度: 第 {pages[0]}-{pages[-1]} 页，"
                f"发现 {len(tid_list)} 条，新增 {len(saved_data)} 条"
            )

        if summary["detail_failures"]:
            raise RuntimeError(
                f"板块 {fid} 有 {summary['detail_failures']} 个详情页抓取失败，"
                "成功数据已保存，请使用 --resume 重试"
            )

        return summary

    @staticmethod
    def _checkpoint_path() -> Path:
        return Path(__file__).resolve().parent.parent / "data" / "backfill_progress.json"

    def _load_backfill_checkpoint(self, year: int, fid: int) -> int:
        path = self._checkpoint_path()
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return int(data.get(f"{year}:{fid}", 0) or 0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as e:
            self.log.warning(f"读取历史补抓检查点失败，将从定位页开始: {e}")
            return 0

    def _save_backfill_checkpoint(self, year: int, fid: int, page: int) -> None:
        path = self._checkpoint_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                data = {}

        data[f"{year}:{fid}"] = page
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)

    def initialize_homepage(self) -> bool:
        """HTTP 模式无需浏览器预热，保留接口兼容 main.py。"""
        return True

    # ---------- 内部 ----------

    def _get_plate_info_batch(self, fid: int) -> Tuple[List[Dict[str, Any]], List[str]]:
        pages = list(range(1, page_num + 1))
        return self._get_plate_info_pages(
            fid,
            pages,
            self.target_date or date(),
            ordered_by_dateline=False,
        )

    def _get_plate_info_pages(
        self,
        fid: int,
        pages: List[int],
        target_date: str,
        ordered_by_dateline: bool,
        fail_on_missing: bool = False,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        t0 = time.time()
        urls = [
            self._build_plate_url(fid, page, ordered_by_dateline)
            for page in pages
        ]
        self.log.info(f"正在批量请求 {len(urls)} 个板块页面（并发 {self.workers}）...")
        responses = self._fetch_many(urls)

        all_info: List[Dict[str, Any]] = []
        all_tids: List[str] = []

        for page, body in zip(pages, responses):
            if not body:
                self.log.warning(f"获取板块 {fid} 第 {page} 页内容失败")
                if fail_on_missing:
                    raise RuntimeError(f"获取板块 {fid} 第 {page} 页内容失败")
                continue
            try:
                info_list, tid_list = self.page_parser.parse_plate_page(body, target_date)
                for info in info_list:
                    info["fid"] = fid
                all_info.extend(info_list)
                all_tids.extend(tid_list)
                self.log.info(f"成功解析板块 {fid} 第 {page} 页，获得 {len(info_list)} 个帖子")
            except Exception as e:
                self.log.error(f"解析板块 {fid} 第 {page} 页时出错: {e}")

        self.log.info(f"批量获取板块列表页耗时: {time.time() - t0:.2f}秒")
        return all_info, all_tids

    def _locate_year_page_range(
        self,
        fid: int,
        year: int,
    ) -> Optional[Tuple[int, int]]:
        """在按发帖时间倒序的列表中二分定位年份页码范围。"""
        cache: Dict[int, Dict[str, Any]] = {}

        def metadata(page: int) -> Dict[str, Any]:
            if page not in cache:
                cache[page] = self._get_ordered_page_metadata(fid, page)
            return cache[page]

        first_page_meta = metadata(1)
        last_page = first_page_meta["last_page"]
        target_start = calendar_date(year, 1, 1)
        target_end = calendar_date(year, 12, 31)

        left, right = 1, last_page
        first_candidate = None
        while left <= right:
            middle = (left + right) // 2
            page_meta = metadata(middle)
            if page_meta["oldest_date"] <= target_end:
                first_candidate = middle
                right = middle - 1
            else:
                left = middle + 1

        if first_candidate is None:
            return None

        left, right = first_candidate, last_page
        last_candidate = None
        while left <= right:
            middle = (left + right) // 2
            page_meta = metadata(middle)
            if page_meta["newest_date"] >= target_start:
                last_candidate = middle
                left = middle + 1
            else:
                right = middle - 1

        if last_candidate is None or first_candidate > last_candidate:
            return None

        # 同一天可能跨页，边界各扩一页后再按年份精确过滤。
        return (
            max(1, first_candidate - 1),
            min(last_page, last_candidate + 1),
        )

    def _get_ordered_page_metadata(self, fid: int, page: int) -> Dict[str, Any]:
        url = self._build_plate_url(fid, page, ordered_by_dateline=True)
        body = self.http.get_html(url)
        if not body:
            raise RuntimeError(f"获取板块 {fid} 第 {page} 页元数据失败")

        info_list = self.page_parser.parse_plate_page_all(body)
        parsed_dates = [
            parsed_date
            for item in info_list
            if (parsed_date := self._parse_plate_date(item.get("date")))
        ]
        if not parsed_dates:
            if page == 1:
                parsed_dates = [calendar_date.today()]
            else:
                raise RuntimeError(
                    f"板块 {fid} 第 {page} 页没有可用于定位的主题日期"
                )

        page_meta = {
            "newest_date": max(parsed_dates),
            "oldest_date": min(parsed_dates),
            "last_page": self.page_parser.parse_last_page(body),
        }
        self.log.info(
            f"定位板块 {fid} 第 {page} 页: "
            f"{page_meta['oldest_date']} - {page_meta['newest_date']}"
        )
        return page_meta

    @staticmethod
    def _parse_plate_date(value: Any) -> Optional[calendar_date]:
        if not value:
            return None
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    @staticmethod
    def _build_plate_url(
        fid: int,
        page: int,
        ordered_by_dateline: bool,
    ) -> str:
        if not ordered_by_dateline:
            return f"https://{domain}/forum-{fid}-{page}.html"

        query = urlencode({
            "mod": "forumdisplay",
            "fid": fid,
            "filter": "author",
            "orderby": "dateline",
            "page": page,
        })
        return f"https://{domain}/forum.php?{query}"

    def _get_thread_details_batch(self, info_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        detailed_data, _ = self._get_thread_details_batch_result(info_list)
        return detailed_data

    def _get_thread_details_batch_result(
        self,
        info_list: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], int]:
        t0 = time.time()
        urls = [f"https://{domain}/forum.php?mod=viewthread&tid={info['tid']}" for info in info_list]
        self.log.info(f"正在批量请求 {len(urls)} 个帖子详情页（并发 {self.workers}）...")
        responses = self._fetch_many(urls)

        results: List[Optional[tuple]] = []
        failures = []
        for i, body in enumerate(responses):
            tid = info_list[i]["tid"]
            if not body:
                self.log.warning(f"获取帖子页面内容失败: {tid}")
                results.append(None)
                failures.append(
                    self._detail_failure(
                        info_list[i],
                        urls[i],
                        "fetch",
                        "request_failed",
                    )
                )
                continue
            try:
                detail = self.page_parser.parse_thread_page(body)
                if detail:
                    results.append((detail, info_list[i]))
                    self.log.debug(f"成功解析帖子 {tid}")
                else:
                    results.append(None)
                    self.log.warning(f"解析帖子页面失败: {tid}")
                    failures.append(
                        self._detail_failure(
                            info_list[i],
                            urls[i],
                            "parse",
                            "invalid_document",
                        )
                    )
            except Exception as e:
                self.log.error(f"解析帖子 {tid} 时出错: {e}")
                results.append(None)
                failures.append(
                    self._detail_failure(
                        info_list[i],
                        urls[i],
                        "parse",
                        type(e).__name__.lower(),
                        str(e),
                    )
                )

        self.log.info(f"_get_thread_details_batch 执行时间: {time.time() - t0:.2f}秒")

        merged = self.data_processor.merge_thread_data(results, info_list)
        cleaned_data = self.data_processor.clean_data(merged)
        cleaned_tids = {str(item.get("tid")) for item in cleaned_data}
        for result in results:
            if result is None:
                continue
            _, basic_info = result
            if str(basic_info.get("tid")) in cleaned_tids:
                continue
            tid = str(basic_info["tid"])
            failures.append(
                self._detail_failure(
                    basic_info,
                    f"https://{domain}/forum.php?mod=viewthread&tid={tid}",
                    "validate",
                    "invalid_record",
                )
            )

        if failures and not self.dry_run:
            try:
                self.failure_store.record(failures)
            except Exception as e:
                self.log.error(f"Sehuatang 失败台账写入失败: {e}")

        failure_count = len(failures)
        return cleaned_data, failure_count

    def retry_failed_details(self) -> Dict[str, int]:
        targets = self.failure_store.due_targets("sehuatang")
        grouped: Dict[int, List[Dict[str, Any]]] = {}
        for target in targets:
            metadata = dict(target.metadata)
            fid = int(metadata.get("fid") or target.partition or 0)
            if not fid or not metadata.get("tid"):
                self.log.warning(f"失败目标缺少 fid/tid，跳过: {target.key}")
                continue
            grouped.setdefault(fid, []).append(metadata)

        summary = {
            "requested": sum(len(items) for items in grouped.values()),
            "failed": 0,
            "saved": 0,
        }
        for fid, info_list in grouped.items():
            detailed_data, failure_count = self._get_thread_details_batch_result(
                info_list
            )
            summary["failed"] += failure_count
            if not detailed_data:
                continue
            saved_data = self.data_manager.filter_and_save_data(
                detailed_data,
                fid,
                strict=True,
                dry_run=self.dry_run,
            )
            summary["saved"] += len(saved_data)
            if not self.dry_run:
                self._clear_detail_failures(detailed_data)
        return summary

    def _detail_failure(
        self,
        info: Dict[str, Any],
        url: str,
        stage: str,
        error_type: str,
        error_message: str = "",
    ) -> CrawlFailure:
        return CrawlFailure(
            source="sehuatang",
            key=str(info["tid"]),
            url=url,
            stage=stage,
            attempts=self.http.settings.retry.attempts,
            error_type=error_type,
            error_message=error_message,
            metadata=dict(info),
        )

    def _clear_detail_failures(self, data_list: List[Dict[str, Any]]) -> None:
        keys = [str(item["tid"]) for item in data_list if item.get("tid")]
        if not keys:
            return
        try:
            self.failure_store.clear("sehuatang", keys)
        except Exception as e:
            self.log.error(f"Sehuatang 失败台账清理失败: {e}")

    def _fetch_many(self, urls: List[str]) -> List[Optional[bytes]]:
        """按输入顺序返回结果。同一 URL 失败位置为 None。"""
        if not urls:
            return []
        results: List[Optional[bytes]] = [None] * len(urls)
        with ThreadPoolExecutor(max_workers=min(self.workers, len(urls))) as pool:
            futures = {pool.submit(self.http.get_html, u): i for i, u in enumerate(urls)}
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    results[idx] = fut.result()
                except Exception as e:
                    self.log.error(f"抓取异常 idx={idx} url={urls[idx]}: {e}")
                    results[idx] = None
        return results

    # ---------- 上下文 ----------

    def close(self):
        self.log.info("爬虫资源已释放")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
