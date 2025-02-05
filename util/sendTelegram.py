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
    bot = None
    log.info("telegram bot is disabled")


def special_char_sub(text):
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
    for i in range(len(old_strs)):
        text = text.replace(old_strs[i], new_strs[i])
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
            content = f"\n{num} {title}\n\n磁力链接：\n{magnet}\n\n发布时间：{post_time}\n\n #{tag_name}"
        else:
            content = f"\n{num} {title}\n\n磁力链接：\n{magnet}\n防115屏蔽压缩包磁链：\n{magnet_115}\n\n发布时间：{post_time}\n\n #{tag_name} "

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
    pass
