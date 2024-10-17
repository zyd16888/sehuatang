import telebot
import re
from telebot.types import InputMediaPhoto
from telebot.util import antiflood
from telebot import apihelper
import time
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
        if magnet_115 is None:
            content = f"\n{num} {title}\n\n磁力链接：\n{magnet}\n\n发布时间：{post_time}\n\n #{tag_name}"
        else:
            content = f"\n{num} {title}\n\n磁力链接：\n{magnet}\n防115屏蔽压缩包磁链：\n{magnet_115}\n\n发布时间：{post_time}\n\n #{tag_name} "
        content = special_char_sub(content)
        media_group = []
        for image in image_list:
            index = image_list.index(image)

            pattern = r"(https?://)([^/]+)/tupian"
            new_domain = image_proxy_url
            image = re.sub(pattern, rf"{new_domain}/tupian", image)
            log.debug(image)

            if index == len(image_list) - 1:
                media_group.append(
                    InputMediaPhoto(
                        media=image, caption=content, parse_mode="markdownV2"
                    )
                )
            else:
                media_group.append(InputMediaPhoto(media=image))

        msg = antiflood(bot.send_media_group, chat_id=tg_chat_id, media=media_group)
        try:
            log.info(
                f"send_media_group, msg id: {' '.join([str(i.json.get('message_id')) for i in msg])}"
            )
        except Exception as e:
            log.debug(media_group)
            log.debug(msg)
            log.error(e)

    if len(data_list) > 0:
        send_message_text = rec_message(data_list, fid)
        msg = antiflood(bot.send_message, chat_id=tg_chat_id, text=send_message_text)
        log.info(f"send telegram message, return msg: {msg.json}")


def rec_message(data_list, fid):
    tag_name = fid_json.get(fid, "other")
    name_list = [data["number"] + " " + data["title"] for data in data_list]
    time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    send_message_text = f"#{tag_name} 抓取完成。\n\n本次抓取共抓取{len(data_list)}个资源 \n\n抓取时间：{time_str} \n\n抓取结果: \n"
    name_str = "\n".join(name_list)
    send_message_text += name_str
    return send_message_text


if __name__ == "__main__":
    pass
