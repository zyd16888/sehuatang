"""只读抓取一页列表和一个详情页，验证 HTTP 通路。"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bs4 import BeautifulSoup  # noqa: E402

from scrapers.http_client import shared_http_client  # noqa: E402


def main():
    client = shared_http_client()
    started = time.monotonic()
    body = client.get_html("https://sehuatang.org/forum-103-1.html")
    print(f"list: len={len(body) if body else 0} elapsed={time.monotonic()-started:.2f}s")
    if not body:
        return

    soup = BeautifulSoup(body, "html.parser")
    threads = soup.find_all(id=lambda value: value and value.startswith("normalthread_"))
    print(f"list.threads={len(threads)}")
    if not threads:
        return

    content_link = threads[0].find(class_="showcontent y")
    tid = content_link.get("id", "").split("_")[-1] if content_link else ""
    if not tid:
        return

    started = time.monotonic()
    detail = client.get_html(
        f"https://sehuatang.org/forum.php?mod=viewthread&tid={tid}"
    )
    print(
        f"detail: tid={tid} len={len(detail) if detail else 0} "
        f"elapsed={time.monotonic()-started:.2f}s"
    )


if __name__ == "__main__":
    main()
