"""
优化的统一运行脚本
支持多种运行模式和更好的错误处理
"""
from util.scheduler_manager import get_scheduler_manager
from util.exceptions import ExceptionHandler
import os
import sys
import time
import signal
import argparse
import datetime
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from util.log_util import log


class ApplicationRunner:
    """应用程序运行器"""

    def __init__(self):
        self.scheduler_manager = None
        self.running = False

        # 注册信号处理器
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """信号处理器"""
        log.info(f"接收到信号 {signum}，正在优雅关闭...")
        self.stop()

    def run_once(self, dry_run=False):
        """运行一次主任务"""
        try:
            log.info("开始执行单次任务")

            import asyncio
            from main import main as main_task

            # 运行主任务
            results = asyncio.run(main_task(dry_run=dry_run))

            log.info("单次任务执行完成")
            return all(
                result.get("status") != "failed"
                for result in results.values()
            )

        except Exception as e:
            ExceptionHandler.handle_and_log(e, "执行单次任务时出错")
            return False

    def run_scheduler(self):
        """运行调度器模式"""
        try:
            log.info("=" * 60)
            log.info("🚀 数据抓取调度器启动")
            log.info(f"📁 工作目录: {os.getcwd()}")
            log.info(f"🐍 Python版本: {sys.version}")
            log.info("=" * 60)

            # 获取调度器管理器
            self.scheduler_manager = get_scheduler_manager()

            # 启动调度器
            if not self.scheduler_manager.start():
                log.error("调度器启动失败")
                return False

            self.running = True
            log.info("📊 调度器运行中，按Ctrl+C优雅退出...")

            # 主循环
            while self.running:
                time.sleep(60)  # 每分钟检查一次

                if self.running:  # 再次检查，避免在sleep期间被停止
                    log.debug("调度器正常运行中...")

            return True

        except Exception as e:
            ExceptionHandler.handle_and_log(e, "运行调度器时出错")
            return False

    def run_backfill(self, year, fids, resume=False, dry_run=False):
        """按年份运行一次历史补抓任务。"""
        try:
            import asyncio
            from main import backfill as backfill_task

            log.info(f"开始执行 {year} 年历史补抓任务")
            return asyncio.run(
                backfill_task(
                    year,
                    fids,
                    resume=resume,
                    dry_run=dry_run,
                )
            )
        except Exception as e:
            ExceptionHandler.handle_and_log(e, "执行历史补抓任务时出错")
            return False

    def run_javbee(self, dry_run=False, retry_failed=False):
        """单独运行 Javbee 数据源。"""
        try:
            import asyncio
            from main import crawl_javbee

            summary = asyncio.run(
                crawl_javbee(
                    force=True,
                    dry_run=dry_run,
                    retry_failed=retry_failed,
                )
            )
            return summary.get("status") != "failed"
        except Exception as e:
            ExceptionHandler.handle_and_log(e, "执行 Javbee 抓取任务时出错")
            return False

    def run_crawl(
        self,
        source="all",
        dry_run=False,
        retry_failed=False,
    ):
        try:
            import asyncio
            from main import crawl_sources
            from scrapers.registry import source_registry

            sources = (
                source_registry.names()
                if source == "all"
                else [source]
            )
            results = asyncio.run(
                crawl_sources(
                    sources,
                    force=True,
                    dry_run=dry_run,
                    retry_failed=retry_failed,
                )
            )
            return all(
                result.get("status") not in {"failed"}
                for result in results.values()
            )
        except Exception as e:
            ExceptionHandler.handle_and_log(e, "执行来源抓取任务时出错")
            return False

    def stop(self):
        """停止应用程序"""
        log.info("正在停止应用程序...")
        self.running = False

        if self.scheduler_manager:
            self.scheduler_manager.stop()

        log.info("应用程序已停止")

    def health_check(self):
        """健康检查"""
        try:
            # 检查配置文件
            from util.read_config import get_config
            from scrapers.core.config import load_source_settings
            from scrapers.registry import source_registry
            config = get_config()
            if not config:
                return False

            for source_name in source_registry.names():
                load_source_settings(config, source_name)

            # 检查日志系统
            log.info("健康检查通过")
            return True

        except Exception as e:
            log.error(f"健康检查失败: {e}")
            return False


def create_argument_parser():
    """创建命令行参数解析器"""
    parser = argparse.ArgumentParser(
        description="数据抓取系统运行器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
运行模式说明:
  scheduler  - 定时调度模式（默认）
  once      - 单次执行模式
  health    - 健康检查模式
  backfill  - 按年份补抓历史数据
  javbee    - 单独抓取 Javbee 数据源

示例:
  python run.py                    # 运行调度器
  python run.py --mode once        # 执行一次任务
  python run.py --mode health      # 健康检查
  python run.py --mode javbee      # 单独抓取 Javbee
  python run.py --mode backfill --year 2025
  python run.py --mode backfill --year 2025 --fid 103 --fid 104
  python run.py --mode backfill --year 2025 --resume
  python run.py crawl --source javbee --dry-run
  python run.py retry-failed --source javbee
        """
    )

    parser.add_argument(
        "action",
        nargs="?",
        choices=["crawl", "retry-failed"],
        help="新式命令入口；未指定时继续使用 --mode",
    )

    parser.add_argument(
        "--mode",
        choices=["scheduler", "once", "health", "backfill", "javbee"],
        default="scheduler",
        help="运行模式 (默认: scheduler)"
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="详细输出模式"
    )

    parser.add_argument(
        "--source",
        choices=["all", "sehuatang", "javbee"],
        help="crawl/retry-failed 的数据来源",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="执行抓取和解析，但不写数据库、不通知、不推进检查点",
    )

    parser.add_argument(
        "--year",
        type=int,
        help="历史补抓年份，仅用于 backfill 模式"
    )

    parser.add_argument(
        "--fid",
        type=int,
        action="append",
        help="历史补抓板块 ID，可重复指定；不传则使用配置文件中的全部板块"
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="从年度补抓检查点继续，仅用于 backfill 模式"
    )

    return parser


def main():
    """主函数"""
    # 解析命令行参数
    parser = create_argument_parser()
    args = parser.parse_args()

    if args.mode == "backfill":
        if args.year is None:
            parser.error("backfill 模式必须指定 --year")
        current_year = datetime.date.today().year
        if args.year < 1900 or args.year > current_year:
            parser.error(f"--year 必须在 1900-{current_year} 之间")

        if args.fid:
            from util.config import fid_list

            configured_fids = {int(fid) for fid in fid_list}
            unknown_fids = sorted(set(args.fid) - configured_fids)
            if unknown_fids:
                parser.error(
                    f"板块 {unknown_fids} 不在配置文件的 fid 列表中"
                )

    # 设置日志级别
    if args.verbose:
        log.set_level("DEBUG")
        log.info("启用详细输出模式")

    # 创建应用程序运行器
    runner = ApplicationRunner()

    try:
        # 根据模式运行
        if args.action == "crawl":
            success = runner.run_crawl(
                source=args.source or "all",
                dry_run=args.dry_run,
            )
        elif args.action == "retry-failed":
            success = runner.run_crawl(
                source=args.source or "javbee",
                dry_run=args.dry_run,
                retry_failed=True,
            )
        elif args.mode == "once":
            success = runner.run_once(dry_run=args.dry_run)
        elif args.mode == "health":
            success = runner.health_check()
        elif args.mode == "backfill":
            success = runner.run_backfill(
                args.year,
                args.fid,
                args.resume,
                args.dry_run,
            )
        elif args.mode == "javbee":
            success = runner.run_javbee(dry_run=args.dry_run)
        else:  # scheduler
            success = runner.run_scheduler()

        # 退出码
        sys.exit(0 if success else 1)

    except KeyboardInterrupt:
        log.info("用户中断程序")
        runner.stop()
        sys.exit(0)
    except Exception as e:
        ExceptionHandler.handle_and_log(e, "程序运行时发生未处理的异常")
        sys.exit(1)


if __name__ == "__main__":
    main()
