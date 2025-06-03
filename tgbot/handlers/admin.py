"""
优化的Telegram Bot管理员处理器
"""
import asyncio
from typing import Dict, Any
from telebot import TeleBot
from telebot.types import Message
from util.config import fid_list, fid_json
from util.log_util import log
from util.exceptions import ExceptionHandler
from util.scheduler_manager import get_scheduler_manager


class AdminHandler:
    """管理员命令处理器"""

    def __init__(self):
        self.scheduler_manager = get_scheduler_manager()

    def handle_start(self, message: Message, bot: TeleBot):
        """处理开始命令"""
        try:
            bot.send_message(message.chat.id, "🤖 欢迎使用数据抓取机器人!")

            help_text = """
📋 **命令菜单：**

🚀 `/start` - 显示帮助信息
🔍 `/c <fid>` - 爬取指定板块 (例: /c 103)
⚙️ `/config` - 查看配置信息
📊 `/status` - 查看任务状态
🔄 `/reload` - 重新加载配置

💡 **提示：** 使用 /c 命令时请确保输入正确的板块ID
            """

            bot.send_message(
                message.chat.id,
                help_text,
                parse_mode="Markdown"
            )

        except Exception as e:
            ExceptionHandler.handle_and_log(e, "处理start命令时出错")
            bot.send_message(message.chat.id, "❌ 命令执行失败")

    def handle_crawl(self, message: Message, bot: TeleBot):
        """处理爬取命令"""
        try:
            msg_parts = message.text.split(' ')

            # 参数验证
            if len(msg_parts) != 2:
                bot.send_message(
                    message.chat.id,
                    "❌ 参数错误！\n\n正确格式: `/c <板块ID>`\n例如: `/c 103`",
                    parse_mode="Markdown"
                )
                return

            fid_str = msg_parts[1]

            # 验证FID是否有效
            if not fid_str.isdigit():
                bot.send_message(message.chat.id, "❌ 板块ID必须是数字")
                return

            fid = int(fid_str)
            valid_fids = [str(f) for f in fid_list]

            if fid_str not in valid_fids:
                available_fids = ", ".join(valid_fids)
                bot.send_message(
                    message.chat.id,
                    f"❌ 板块ID不在配置中\n\n可用的板块ID: {available_fids}"
                )
                return

            # 开始爬取
            bot.send_message(message.chat.id, f"🚀 开始爬取板块 {fid}，请稍候...")

            # 执行爬取任务
            from main import crawl_forum_section
            result = asyncio.run(crawl_forum_section(fid))

            # 发送结果
            bot.send_message(message.chat.id, f"✅ 爬取完成\n\n{result}")

        except Exception as e:
            ExceptionHandler.handle_and_log(e, "处理爬取命令时出错")
            bot.send_message(message.chat.id, "❌ 爬取失败，请查看日志")

    def handle_config(self, message: Message, bot: TeleBot):
        """处理配置查看命令"""
        try:
            if not fid_json:
                bot.send_message(message.chat.id, "❌ 未找到板块配置")
                return

            config_text = "⚙️ **当前板块配置：**\n\n"
            for fid, name in fid_json.items():
                config_text += f"🔸 `{fid}`: {name}\n"

            bot.send_message(message.chat.id, config_text,
                             parse_mode="Markdown")

        except Exception as e:
            ExceptionHandler.handle_and_log(e, "处理配置命令时出错")
            bot.send_message(message.chat.id, "❌ 获取配置失败")

    def handle_status(self, message: Message, bot: TeleBot):
        """处理状态查看命令"""
        try:
            jobs = self.scheduler_manager.get_job_status()

            if not jobs:
                bot.send_message(message.chat.id, "📊 当前没有运行的定时任务")
                return

            status_text = "📊 **任务状态：**\n\n"
            for job in jobs:
                next_run = job.get('next_run_time', '未知')
                if next_run and next_run != 'None':
                    next_run = next_run.replace('T', ' ').split('.')[0]
                else:
                    next_run = "暂停"

                status_text += f"🔸 **{job['name']}**\n"
                status_text += f"   ID: `{job['id']}`\n"
                status_text += f"   下次执行: {next_run}\n\n"

            bot.send_message(message.chat.id, status_text,
                             parse_mode="Markdown")

        except Exception as e:
            ExceptionHandler.handle_and_log(e, "处理状态命令时出错")
            bot.send_message(message.chat.id, "❌ 获取状态失败")

    def handle_reload(self, message: Message, bot: TeleBot):
        """处理重新加载配置命令"""
        try:
            from util.read_config import _config_manager
            _config_manager.reload_config()

            bot.send_message(message.chat.id, "✅ 配置已重新加载")

        except Exception as e:
            ExceptionHandler.handle_and_log(e, "重新加载配置时出错")
            bot.send_message(message.chat.id, "❌ 重新加载配置失败")


# 创建全局处理器实例
admin_handler = AdminHandler()

# 兼容旧版本的函数


def admin_user(message: Message, bot: TeleBot):
    """兼容旧版本的开始命令处理"""
    admin_handler.handle_start(message, bot)


def crawl_plate(message: Message, bot: TeleBot):
    """兼容旧版本的爬取命令处理"""
    admin_handler.handle_crawl(message, bot)

def read_config(message: Message, bot: TeleBot):
    """兼容旧版本的配置命令处理"""
    admin_handler.handle_config(message, bot)


def status_command(message: Message, bot: TeleBot):
    """状态命令处理"""
    admin_handler.handle_status(message, bot)


def reload_command(message: Message, bot: TeleBot):
    """重新加载命令处理"""
    admin_handler.handle_reload(message, bot)

