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

    def run_once(self):
        """运行一次主任务"""
        try:
            log.info("开始执行单次任务")

            import asyncio
            from main import main as main_task

            # 运行主任务
            asyncio.run(main_task())

            log.info("单次任务执行完成")

        except Exception as e:
            ExceptionHandler.handle_and_log(e, "执行单次任务时出错")
            return False

        return True

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

    def run_bot(self):
        """运行Telegram Bot"""
        try:
            log.info("🤖 启动Telegram Bot...")

            from bot import run as run_bot
            run_bot()

        except Exception as e:
            ExceptionHandler.handle_and_log(e, "运行Telegram Bot时出错")
            return False

        return True

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
            config = get_config()
            if not config:
                return False

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
  bot       - Telegram Bot模式
  health    - 健康检查模式

示例:
  python run.py                    # 运行调度器
  python run.py --mode once        # 执行一次任务
  python run.py --mode bot         # 运行Telegram Bot
  python run.py --mode health      # 健康检查
        """
    )

    parser.add_argument(
        "--mode",
        choices=["scheduler", "once", "bot", "health"],
        default="scheduler",
        help="运行模式 (默认: scheduler)"
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="详细输出模式"
    )

    return parser


def main():
    """主函数"""
    # 解析命令行参数
    parser = create_argument_parser()
    args = parser.parse_args()

    # 设置日志级别
    if args.verbose:
        log.info("启用详细输出模式")

    # 创建应用程序运行器
    runner = ApplicationRunner()

    try:
        # 根据模式运行
        if args.mode == "once":
            success = runner.run_once()
        elif args.mode == "bot":
            success = runner.run_bot()
        elif args.mode == "health":
            success = runner.health_check()
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