"""
统一的调度器管理模块
支持多种调度方式和配置
"""
import asyncio
import datetime
import traceback
from typing import Callable, Optional, List
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from util.log_util import log
from util.read_config import get_config
from util.exceptions import ExceptionHandler


class SchedulerManager:
    """调度器管理器"""
    
    def __init__(self):
        self.scheduler: Optional[BackgroundScheduler] = None
        self.is_running = False
        
    def initialize(self) -> bool:
        """初始化调度器"""
        try:
            if self.scheduler is not None:
                log.warning("调度器已经初始化")
                return True
            
            self.scheduler = BackgroundScheduler()
            log.info("调度器初始化成功")
            return True
            
        except Exception as e:
            log.error(f"调度器初始化失败: {e}")
            return False
    
    def add_cron_job(
        self,
        func: Callable,
        cron_expression: str,
        job_id: str,
        job_name: str = "",
        max_instances: int = 1,
        misfire_grace_time: int = 600
    ) -> bool:
        """
        添加Cron定时任务
        
        Args:
            func: 要执行的函数
            cron_expression: Cron表达式
            job_id: 任务ID
            job_name: 任务名称
            max_instances: 最大实例数
            misfire_grace_time: 错过执行时间的宽限期（秒）
        """
        try:
            if self.scheduler is None:
                log.error("调度器未初始化")
                return False
            
            self.scheduler.add_job(
                func,
                CronTrigger.from_crontab(cron_expression),
                id=job_id,
                name=job_name or job_id,
                max_instances=max_instances,
                misfire_grace_time=misfire_grace_time
            )
            
            log.info(f"Cron任务添加成功: {job_name} ({cron_expression})")
            return True
            
        except Exception as e:
            log.error(f"添加Cron任务失败: {e}")
            return False
    
    def add_interval_job(
        self,
        func: Callable,
        seconds: int,
        job_id: str,
        job_name: str = "",
        max_instances: int = 1
    ) -> bool:
        """
        添加间隔定时任务
        
        Args:
            func: 要执行的函数
            seconds: 间隔秒数
            job_id: 任务ID
            job_name: 任务名称
            max_instances: 最大实例数
        """
        try:
            if self.scheduler is None:
                log.error("调度器未初始化")
                return False
            
            self.scheduler.add_job(
                func,
                IntervalTrigger(seconds=seconds),
                id=job_id,
                name=job_name or job_id,
                max_instances=max_instances
            )
            
            log.info(f"间隔任务添加成功: {job_name} (每{seconds}秒)")
            return True
            
        except Exception as e:
            log.error(f"添加间隔任务失败: {e}")
            return False
    
    def start(self) -> bool:
        """启动调度器"""
        try:
            if self.scheduler is None:
                log.error("调度器未初始化")
                return False
            
            if self.is_running:
                log.warning("调度器已经在运行")
                return True
            
            self.scheduler.start()
            self.is_running = True
            
            # 打印任务信息
            self._print_job_info()
            
            log.info("调度器启动成功")
            return True
            
        except Exception as e:
            log.error(f"调度器启动失败: {e}")
            return False
    
    def stop(self) -> bool:
        """停止调度器"""
        try:
            if self.scheduler is None or not self.is_running:
                log.info("调度器未运行")
                return True
            
            self.scheduler.shutdown()
            self.is_running = False
            
            log.info("调度器已停止")
            return True
            
        except Exception as e:
            log.error(f"停止调度器失败: {e}")
            return False
    
    def _print_job_info(self):
        """打印任务信息"""
        if self.scheduler is None:
            return
        
        jobs = self.scheduler.get_jobs()
        if not jobs:
            log.info("没有配置任务")
            return
        
        log.info("已配置的任务:")
        for job in jobs:
            if job.next_run_time:
                next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
                log.info(f"  - {job.name} (ID: {job.id}) 下次执行: {next_run}")
            else:
                log.info(f"  - {job.name} (ID: {job.id}) 状态: 暂停")
    
    def get_job_status(self) -> List[dict]:
        """获取任务状态"""
        if self.scheduler is None:
            return []
        
        jobs = []
        for job in self.scheduler.get_jobs():
            job_info = {
                "id": job.id,
                "name": job.name,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger)
            }
            jobs.append(job_info)
        
        return jobs


def run_async_task(coro):
    """在单独的事件循环中运行协程"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        # 确保清理事件循环
        if loop.is_running():
            loop.stop()
        if not loop.is_closed():
            loop.close()


def create_async_job_wrapper(async_func: Callable, job_name: str = ""):
    """创建异步任务包装器"""
    def wrapper():
        try:
            start_time = datetime.datetime.now()
            log.info(f"开始执行任务: {job_name}")
            
            run_async_task(async_func())
            
            end_time = datetime.datetime.now()
            elapsed_time = (end_time - start_time).total_seconds()
            log.info(f"任务执行完成: {job_name}，耗时: {elapsed_time:.2f}秒")
            
        except Exception as e:
            ExceptionHandler.handle_and_log(e, f"执行任务 {job_name} 时出错")
    
    return wrapper


def setup_default_scheduler() -> SchedulerManager:
    """设置默认调度器"""
    manager = SchedulerManager()
    
    if not manager.initialize():
        raise RuntimeError("调度器初始化失败")
    
    # 优先使用每来源独立调度；未配置时兼容旧的全量 cron。
    full_config = get_config() or {}
    crawler_sources = ((full_config.get("crawler") or {}).get("sources") or {})
    source_jobs = 0
    if crawler_sources:
        from main import crawl_sources
        from scrapers.registry import source_registry

        for source_name in source_registry.names():
            source_config = crawler_sources.get(source_name) or {}
            cron_expression = (source_config.get("schedule") or {}).get("cron")
            enabled = source_config.get(
                "enabled",
                source_registry.is_enabled(full_config, source_name),
            )
            if not enabled or not cron_expression:
                continue

            async def source_task(name=source_name):
                return await crawl_sources([name])

            wrapped_task = create_async_job_wrapper(
                source_task,
                f"{source_name} 抓取任务",
            )
            if manager.add_cron_job(
                wrapped_task,
                cron_expression,
                f"crawl_{source_name}",
                f"{source_name} 抓取任务",
                max_instances=1,
            ):
                source_jobs += 1

    if source_jobs:
        return manager

    # 旧配置兼容：所有已启用来源仍由一个任务顺序执行。
    schedule_config = get_config("schedule", {})
    cron_expression = schedule_config.get("schedule_cron")
    
    if cron_expression:
        # 导入主任务函数
        try:
            from main import main as main_task
            
            # 创建异步任务包装器
            wrapped_task = create_async_job_wrapper(main_task, "数据抓取任务")
            
            # 添加任务
            manager.add_cron_job(
                wrapped_task,
                cron_expression,
                "main_crawl_task",
                "数据抓取任务"
            )
            
        except ImportError as e:
            log.error(f"导入主任务函数失败: {e}")
    
    return manager


# 全局调度器实例
_scheduler_manager: Optional[SchedulerManager] = None


def get_scheduler_manager() -> SchedulerManager:
    """获取全局调度器管理器"""
    global _scheduler_manager
    if _scheduler_manager is None:
        _scheduler_manager = setup_default_scheduler()
    return _scheduler_manager
