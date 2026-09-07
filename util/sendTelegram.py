import telebot
import re
from telebot.types import InputMediaPhoto
from telebot.util import antiflood
from telebot import apihelper
import time
import math
from util.log_util import log
from util.config import (
    tg_bot_token,
    tg_chat_id,
    fid_json,
    tg_enable,
    proxy,
    image_proxy_url,
)

if tg_enable:
    bot = telebot.TeleBot(tg_bot_token)
    if proxy is not None:
        apihelper.proxy = {
            "http": proxy,
            "https": proxy,
        }
else:
    bot = telebot.TeleBot(tg_bot_token)
    if proxy is not None:
        apihelper.proxy = {
            "http": proxy,
            "https": proxy,
        }
    log.info("telegram bot send image is disabled")


def special_char_sub(text):
    # 用于保存 code 块
    protected_code = {}

    # 替换函数，用于 re.sub 中
    def replace_code_block(match):
        idx = len(protected_code)
        placeholder = f"CODEBLOCKPLACEHOLDER{idx}UNIQUE"
        protected_code[placeholder] = match.group(0)  # 包括反引号的完整内容
        return placeholder

    # 第一步：用正则替换所有 `code` 块为占位符
    text = re.sub(r"`[^`]+`", replace_code_block, text)

    # 第二步：转义 MarkdownV2 特殊字符
    old_strs = [
        "_",
        "*",
        "[",
        "]",
        "(",
        ")",
        "~",
        "`",
        ">",
        "#",
        "+",
        "-",
        "=",
        "|",
        "{",
        "}",
        ".",
        "!",
    ]
    new_strs = [
        r"\_",
        r"\*",
        r"\[",
        r"\]",
        r"\(",
        r"\)",
        r"\~",
        r"\`",
        r"\>",
        r"\#",
        r"\+",
        r"\-",
        r"\=",
        r"\|",
        r"\{",
        r"\}",
        r"\.",
        r"\!",
    ]
    for old, new in zip(old_strs, new_strs):
        text = text.replace(old, new)

    # 第三步：还原 code 块
    for placeholder, code in protected_code.items():
        text = text.replace(placeholder, code)

    return text


def send_media_group(data_list, fid):
    tag_name = fid_json.get(fid, "other")

    for data in data_list:
        magnet = data["magnet"]
        magnet_115 = data["magnet_115"]
        title = data["title"]
        num = data["number"]
        post_time = data["post_time"]
        image_list = data["img"]

        # 处理文本信息
        if magnet_115 is None:
            content = f"\n{num} {title}\n\n磁力链接：\n`{magnet}`\n\n发布时间：{post_time}\n\n #{tag_name}"
        else:
            content = f"\n{num} {title}\n\n磁力链接：\n`{magnet}`\n防115屏蔽压缩包磁链：\n`{magnet_115}`\n\n发布时间：{post_time}\n\n #{tag_name} "

        content = special_char_sub(content)

        # 限制每次最多发送10张图片
        batch_size = 10
        num_batches = math.ceil(len(image_list) / batch_size)

        for batch_index in range(num_batches):
            media_group = []
            batch_images = image_list[
                batch_index * batch_size : (batch_index + 1) * batch_size
            ]

            for index, image in enumerate(batch_images):
                # 替换图片域名
                pattern = r"(https?://)([^/]+)/tupian"
                image = re.sub(pattern, rf"{image_proxy_url}/tupian", image)
                log.debug(image)

                # 只有最后一张图片带 caption（仅在最后一组发送）
                if batch_index == num_batches - 1 and index == len(batch_images) - 1:
                    media_group.append(
                        InputMediaPhoto(
                            media=image, caption=content, parse_mode="markdownV2"
                        )
                    )
                else:
                    media_group.append(InputMediaPhoto(media=image))

            try:
                msg = antiflood(
                    bot.send_media_group, chat_id=tg_chat_id, media=media_group
                )
                log.info(
                    f"send_media_group, batch {batch_index+1}/{num_batches}, msg id: "
                    f"{' '.join([str(i.json.get('message_id')) for i in msg])}"
                )
            except Exception as e:
                log.error(f"Failed to send media group: {e}")
                log.debug(media_group)

    # 发送文本消息
    # if len(data_list) > 0:
    #     send_message_text = rec_message(data_list, fid)

    # if send_message_text:  # 确保消息内容不为空
    #     msg = antiflood(
    #         bot.send_message, chat_id=tg_chat_id, text=send_message_text
    #     )
    #     log.info(f"send telegram message, return msg: {msg.json}")
    # else:
    #     log.debug("rec_message returned an empty message, skipping send_message")


def _send_media_batches(image_list, content):
    """按每批 10 张发送图片组，caption 挂在最后一张；无图时发纯文本。

    返回是否至少成功发送了一条消息。
    """
    if not image_list:
        try:
            antiflood(
                bot.send_message,
                chat_id=tg_chat_id,
                text=content,
                parse_mode="markdownV2",
            )
            return True
        except Exception as e:
            log.error(f"Failed to send text message: {e}")
            return False

    batch_size = 10
    num_batches = math.ceil(len(image_list) / batch_size)
    sent_any = False
    for batch_index in range(num_batches):
        media_group = []
        batch_images = image_list[
            batch_index * batch_size : (batch_index + 1) * batch_size
        ]
        for index, image in enumerate(batch_images):
            if batch_index == num_batches - 1 and index == len(batch_images) - 1:
                media_group.append(
                    InputMediaPhoto(
                        media=image, caption=content, parse_mode="markdownV2"
                    )
                )
            else:
                media_group.append(InputMediaPhoto(media=image))
        try:
            antiflood(bot.send_media_group, chat_id=tg_chat_id, media=media_group)
            sent_any = True
        except Exception as e:
            log.error(f"Failed to send media group: {e}")

    if not sent_any:
        # 图片全部失败时退回纯文本，保证通知不丢
        try:
            antiflood(
                bot.send_message,
                chat_id=tg_chat_id,
                text=content,
                parse_mode="markdownV2",
            )
            sent_any = True
        except Exception as e:
            log.error(f"Failed to send fallback text message: {e}")
    return sent_any


def send_x1080x_media_group(data_list):
    """推送 x1080x 增量新资源，格式与 sehuatang 推送保持一致。"""
    for data in data_list:
        code = data.get("code") or ""
        title = data.get("title") or ""
        magnet = data.get("magnet") or ""
        date = data.get("date") or ""
        section = str(data.get("section") or "").strip()
        image_list = list(data.get("img") or [])

        header = f"{code} {title}".strip()
        tags = "#x1080x" + (f" #{section}" if section else "")
        content = (
            f"\n{header}\n\n磁力链接：\n`{magnet}`\n\n"
            f"发布日期：{date}\n\n{tags}"
        )
        content = special_char_sub(content)
        _send_media_batches(image_list, content)


MAX_MESSAGE_LENGTH = 4000  # 预留一些字符，防止超限

def rec_message(data_list, fid):
    if not data_list:
        log.debug("data_list is empty, skipping send_message")
        return  # 直接返回，避免发送空消息

    tag_name = fid_json.get(fid, "other")
    name_list = [data["number"] + " " + data["title"] for data in data_list]
    time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    # 先发送摘要
    summary_text = (
        f"#{tag_name} 抓取完成。\n\n"
        f"本次抓取共 {len(data_list)} 个资源\n\n"
        f"抓取时间：{time_str}\n\n"
        f"抓取结果：\n"
    )

    log.debug(f"Sending summary message:\n{summary_text}")
    antiflood(bot.send_message, chat_id=tg_chat_id, text=summary_text)

    # 处理抓取结果，分批发送
    current_batch = []
    current_length = 0

    for name in name_list:
        if current_length + len(name) + 1 > MAX_MESSAGE_LENGTH:
            if current_batch:  # 确保 current_batch 不为空
                batch_text = "\n".join(current_batch)
                log.debug(f"Sending batch message:\n{batch_text}")
                antiflood(bot.send_message, chat_id=tg_chat_id, text=batch_text)

            # 清空批次，准备新的
            current_batch = []
            current_length = 0

        # 加入当前批次
        current_batch.append(name)
        current_length += len(name) + 1  # 计算换行符的长度

    # 发送剩余部分
    if current_batch:  # 确保最后一条消息不为空
        batch_text = "\n".join(current_batch)
        log.debug(f"Sending final batch message:\n{batch_text}")
        antiflood(bot.send_message, chat_id=tg_chat_id, text=batch_text)


if __name__ == "__main__":
    text = """
com-452 中出しされたパパ活美少女 「ゴムして」って言ったよね

磁力链接：
`magnet:?xt=urn:btih:B1B5FB29ADCC2A05EECB7539AC70BFF455AB7CA7`

发布时间：2025-05-26 12:32:48

"""
    text = special_char_sub(text)
    print(text)
    pass
