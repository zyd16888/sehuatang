"""
通知管理器模块
统一管理消息发送，包括Telegram等通知方式
"""
from typing import List, Dict, Any
from util.log_util import log
from util.config import tg_enable
from util.sendTelegram import send_media_group, send_x1080x_media_group, rec_message


class NotificationManager:
    """通知管理器类"""

    def __init__(self):
        self.log = log
        self.tg_enable = tg_enable

    def send_notifications(self, data_list: List[Dict[str, Any]], fid: int) -> str:
        """
        发送通知消息
        
        Args:
            data_list: 数据列表
            fid: 板块ID
            
        Returns:
            通知结果消息
        """
        if not data_list:
            return "没有新的数据"

        try:
            if self.tg_enable:
                self.log.info("发送Telegram通知")
                send_media_group(data_list, fid)
                rec_message(data_list, fid)

            return True

        except Exception as e:
            self.log.error(f"发送通知时出错: {e}")
            return f"通知发送失败: {str(e)}"

    def send_x1080x_notifications(self, data_list: List[Dict[str, Any]]) -> bool:
        """推送 x1080x 增量新资源；未启用 TG 或列表为空时静默跳过。"""
        if not data_list or not self.tg_enable:
            return False
        try:
            self.log.info(f"发送 x1080x Telegram 通知，共 {len(data_list)} 条")
            send_x1080x_media_group(data_list)
            return True
        except Exception as e:
            self.log.error(f"发送 x1080x 通知时出错: {e}")
            return False

    def is_notification_enabled(self) -> bool:
        """检查是否启用了通知功能"""
        return self.tg_enable
