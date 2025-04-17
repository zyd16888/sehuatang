import os
import asyncio
import sys
import traceback
import datetime

# APScheduler相关导入
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from util.log_util import log
from util.config import schedule_cron


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

def run_sht_task():
    """执行98tang实时任务"""
    try:
        start_time = datetime.datetime.now()
        from main import main as run_main
        log.info("开始执行98tang实时任务")
        run_async_task(run_main())
        end_time = datetime.datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        log.info(f"98tang实时任务执行完成，耗时: {elapsed_time:.2f}秒")
    except Exception as e:
        log.error(f"失败: {str(e)}")
        return


def init_scheduler():
    """初始化并启动APScheduler调度器"""
    try:
        # 记录启动信息
        log.info("=" * 50)
        log.info("APScheduler定时任务调度器启动")
        log.info(f"当前工作目录: {os.getcwd()}")
        log.info(f"Python版本: {sys.version}")
        log.info("=" * 50)

        # 创建调度器
        scheduler = BackgroundScheduler()

        log.info(f"读取到定时任务配置: {schedule_cron}")


        # 添加实时任务，直接使用cron表达式
        scheduler.add_job(
            run_sht_task,
            CronTrigger.from_crontab(schedule_cron),
            id='realtime_task',
            name='98tang实时任务',
            max_instances=1,  # 确保同一时间只有一个任务在执行
            misfire_grace_time=600  # 任务错过执行时间后的宽限期（秒）
        )

        # 启动调度器
        scheduler.start()
        log.info("调度器已成功启动")

        # 打印下一次执行的时间
        for job in scheduler.get_jobs():
            next_run_time = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
            log.info(f"任务 '{job.name}' 下一次执行时间: {next_run_time}")

        return scheduler

    except Exception as e:
        log.error(f"初始化调度器时发生异常: {str(e)}")
        log.error(f"详细错误: {traceback.format_exc()}")
        raise

def main():
    """主函数"""
    try:
        # 初始化并启动调度器
        scheduler = init_scheduler()

        # 保持主线程运行
        log.info("调度器运行中，按Ctrl+C终止...")
        try:
            # 主线程保持运行
            while True:
                import time
                time.sleep(300)

                # 每分钟打印一次任务状态
                log.info("调度器正常运行中...")
                for job in scheduler.get_jobs():
                    next_run_time = job.next_run_time.strftime(
                        "%Y-%m-%d %H:%M:%S")
                    log.info(f"任务 '{job.name}' 下一次执行时间: {next_run_time}")

        except KeyboardInterrupt:
            log.info("检测到用户中断，正在关闭调度器...")
            scheduler.shutdown()
            log.info("调度器已关闭")

    except Exception as e:
        log.error(f"主程序运行时发生异常: {str(e)}")
        log.error(f"详细错误: {traceback.format_exc()}")
        raise


if __name__ == "__main__":
    main()