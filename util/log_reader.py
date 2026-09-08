"""有限扫描当前日志文件，按记录筛选，保留同一条日志的异常堆栈。"""
import os
import re
from pathlib import Path

from util.log_util import LOG_MODULES


LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
MAX_SCAN_BYTES = 4 * 1024 * 1024
_HEADER = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - ([A-Z]+) - "
    r"(?:\[module=([a-z_0-9]+)\] )?"
)


def _reverse_lines(handle, stats, max_bytes):
    handle.seek(0, os.SEEK_END)
    position = handle.tell()
    remaining = min(position, max_bytes)
    carry = b""
    first = True
    while remaining:
        amount = min(64 * 1024, remaining)
        position -= amount
        handle.seek(position)
        data = handle.read(amount)
        stats["scanned_bytes"] += len(data)
        remaining -= amount
        parts = (data + carry).split(b"\n")
        carry = parts.pop(0)
        for raw in reversed(parts):
            if first and not raw:
                first = False
                continue
            first = False
            yield raw.rstrip(b"\r").decode("utf-8", errors="replace")
    stats["scan_limited"] = position > 0
    if position == 0 and carry:
        yield carry.rstrip(b"\r").decode("utf-8", errors="replace")


def read_log_tail(path: Path, limit=200, *, level="", module="", max_bytes=MAX_SCAN_BYTES):
    stats = {"scanned_bytes": 0, "scan_limited": False}
    records = []
    continuation = []
    try:
        with path.open("rb") as handle:
            for line in _reverse_lines(handle, stats, max_bytes):
                header = _HEADER.match(line)
                if header is None:
                    continuation.append(line)
                    continue
                entry_level, entry_module = header.groups()
                entry_module = entry_module if entry_module in LOG_MODULES else "unclassified"
                record = [line, *reversed(continuation)]
                continuation.clear()
                if (not level or entry_level == level) and (not module or entry_module == module):
                    records.append(record)
                    if len(records) >= limit:
                        break
            else:
                # 文件开头的无格式旧日志仍可查看；扫描窗口外缺少头部的堆栈不冒充独立记录。
                if not stats["scan_limited"] and not level and module in ("", "unclassified"):
                    records.extend([line] for line in continuation[:max(0, limit - len(records))])
    except FileNotFoundError:
        pass
    return {"lines": [line for record in reversed(records) for line in record],
            "matched_count": len(records), **stats}
