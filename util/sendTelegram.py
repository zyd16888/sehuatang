"""Telegram 消费端：格式化、图片下载与发送，导入模块不会创建 Bot。"""
import re
import time

from util.log_util import log
from util.read_config import get_config


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


def _fetch_hotlinked_image(url, timeout=20):
    """下载有防盗链的图片，返回字节；失败返回 None。

    x1080x 的封面图床（hxmmdd.com 等）校验 Referer：无 Referer 直接 404，
    带图床自身域名即放行（实测有效，且不依赖频繁轮换的论坛镜像域名）。
    Telegram 服务器拉 URL 时不带 Referer，所以必须由我们下载后按文件上传。
    """
    from urllib.parse import urlsplit

    from curl_cffi import requests as curl_requests

    parts = urlsplit(str(url or ""))
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    try:
        response = curl_requests.get(
            url,
            headers={"Referer": f"{parts.scheme}://{parts.netloc}/"},
            impersonate="chrome110",
            timeout=timeout,
        )
        content_type = str(response.headers.get("content-type") or "")
        if (
            response.status_code == 200
            and response.content
            and content_type.startswith("image/")
        ):
            return response.content
        log.warning(
            f"图片下载失败: status={response.status_code} "
            f"type={content_type} url={url}"
        )
    except Exception as e:
        log.warning(f"图片下载异常: url={url} error={e}")
    return None



def _text_parts(text):
    return [{"method": "text", "text": text[start:start + 4000]}
            for start in range(0, len(text), 4000)]


class TelegramSender:
    def __init__(self, bot=None, chat_id=None, min_interval=None, sleeper=time.sleep, stop_event=None):
        self._bot = bot
        self.chat_id = chat_id if chat_id is not None else get_config("sendMessage.tg_chat_id")
        self.min_interval = max(0, float(min_interval if min_interval is not None else
                                       get_config("sendMessage.queue.min_interval_seconds", 1)))
        self._sleep = sleeper
        self._next_send_at = 0
        self._stop_event = stop_event

    def _check_stopped(self):
        if self._stop_event is not None and self._stop_event.is_set():
            raise InterruptedError("通知发送已停止")

    def _get_bot(self):
        if self._bot is None:
            import telebot
            from telebot import apihelper
            token = str(get_config("sendMessage.tg_bot_token", "") or "")
            if not token or not self.chat_id:
                raise ValueError("Telegram Token 或接收目标未配置")
            self._bot = telebot.TeleBot(token)
            proxy_url = get_config("proxy.proxy_url") if get_config("proxy.proxy_enable", False) else None
            apihelper.proxy = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        return self._bot

    def _call(self, method, **kwargs):
        self._check_stopped()
        delay = self._next_send_at - time.monotonic()
        if delay > 0:
            if self._stop_event is None:
                self._sleep(delay)
            elif self._stop_event.wait(delay):
                raise InterruptedError("通知发送已停止")
        try:
            return getattr(self._get_bot(), method)(chat_id=self.chat_id, timeout=30, **kwargs)
        finally:
            self._next_send_at = time.monotonic() + self.min_interval

    def _parts(self, job):
        data = job.payload
        fid = data.get("fid")
        names = get_config("sehuatang.fid", {}) or {}
        tag = names.get(fid, names.get(str(fid), "other"))
        if job.kind == "summary":
            captured_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(job.created_at))
            rows = data["names"]
            summary = f"#{tag} 抓取完成。\n\n本次抓取共 {len(rows)} 个资源\n\n抓取时间：{captured_at}\n\n抓取结果：\n"
            return _text_parts(summary) + _text_parts("\n".join(rows))
        record = data["record"]
        images = list(record.get("img") or [])
        if job.source == "x1080x":
            header = f"{record.get('code') or ''} {record.get('title') or ''}".strip()
            tag = "#x1080x" + (f" #{record['section']}" if record.get("section") else "")
            body = f"\n{header}\n\n磁力链接：\n`{record.get('magnet') or ''}`\n\n发布日期：{record.get('date') or ''}\n\n{tag}"
        else:
            body = f"\n{record.get('number') or ''} {record.get('title') or ''}\n\n磁力链接：\n`{record.get('magnet') or ''}`\n"
            if record.get("magnet_115"):
                body += f"防115屏蔽压缩包磁链：\n`{record['magnet_115']}`\n"
            body += f"\n发布时间：{record.get('post_time') or ''}\n\n #{tag}"
            origin = str(get_config("sendMessage.image_proxy_url", "") or "").rstrip("/")
            if origin:
                images = [re.sub(r"https?://[^/]+/tupian", lambda _: origin + "/tupian", url) for url in images]
        formatted = special_char_sub(body)
        if not images:
            return ([{"method": "text", "text": formatted, "parse_mode": "MarkdownV2"}]
                    if len(formatted) <= 4000 else _text_parts(body.replace("`", "")))
        parts = []
        for start in range(0, len(images), 10):
            last = start + 10 >= len(images)
            caption = formatted if last and len(formatted) <= 1024 else None
            parts.append({"method": "media", "images": images[start:start + 10],
                          "caption": caption, "download": job.source == "x1080x",
                          "fallback_text": body.replace("`", "") if caption else None})
        if len(formatted) > 1024:
            parts.extend(_text_parts(body.replace("`", "")))
        return parts

    def _send_part(self, part):
        if part["method"] == "text":
            return self._call("send_message", text=part["text"], parse_mode=part.get("parse_mode"))
        from telebot.types import InputMediaPhoto
        images = part["images"]
        if part["download"]:
            prepared = []
            for url in images:
                self._check_stopped()
                payload = _fetch_hotlinked_image(url)
                if payload is not None:
                    prepared.append(payload)
            images = prepared
        caption = part.get("caption")
        if not images:
            return self._call("send_message", text=caption, parse_mode="MarkdownV2") if caption else []
        try:
            if len(images) == 1:
                return self._call("send_photo", photo=images[0], caption=caption, parse_mode="MarkdownV2")
            media = [InputMediaPhoto(media=image,
                     caption=caption if index == len(images)-1 else None, parse_mode="MarkdownV2")
                     for index, image in enumerate(images)]
            return self._call("send_media_group", media=media)
        except Exception as exc:
            # 无效图片可降级为文本；限流和网络错误交回队列安排重试。
            if getattr(exc, "error_code", None) == 400 and part.get("fallback_text"):
                return self._call("send_message", text=part["fallback_text"])
            if getattr(exc, "error_code", None) == 400 and not caption:
                log.warning("图片组无法发送，跳过该组；文字说明由后续分组发送")
                return []
            raise

    def __call__(self, job):
        if job.parts is None:
            job.parts = self._parts(job)
        while job.next_part < len(job.parts):
            self._check_stopped()
            result = self._send_part(job.parts[job.next_part])
            messages = result if isinstance(result, (list, tuple)) else [result]
            job.message_ids.extend(getattr(message, "message_id", None) for message in messages if message)
            # 一个分组成功后马上推进；后续分组失败时不重发已经确认成功的分组。
            job.next_part += 1
        log.info(f"通知已发送: source={job.source} key={job.key} parts={job.next_part}")
