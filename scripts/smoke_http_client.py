"""http_client 烟雾测试：抓一页列表 + 抓一篇详情，确认通路。"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bs4 import BeautifulSoup  # noqa: E402

from scrapers.http_client import http_client  # noqa: E402


def main():
    t0 = time.time()
    body = http_client.get_html("https://sehuatang.org/forum-103-1.html")
    elapsed = time.time() - t0
    print(f"list: len={len(body) if body else 0}  elapsed={elapsed:.2f}s")
    if not body:
        return

    soup = BeautifulSoup(body, "html.parser")
    title = soup.find("title")
    print(f"list.title={title.get_text(strip=True) if title else ''}")
    threads = soup.find_all(id=lambda v: v and v.startswith("normalthread_"))
    print(f"list.threads={len(threads)}")
    if not threads:
        return

    sc = threads[0].find(class_="showcontent y")
    tid = sc.get("id", "").split("_")[-1] if sc else ""
    print(f"sample tid={tid}")

    t1 = time.time()
    body2 = http_client.get_html(f"https://sehuatang.org/forum.php?mod=viewthread&tid={tid}")
    print(f"detail: len={len(body2) if body2 else 0}  elapsed={time.time()-t1:.2f}s")
    if body2:
        s2 = BeautifulSoup(body2, "html.parser")
        h1 = s2.find("h1", class_="ts")
        print(f"detail.title={h1.find('span').get_text(strip=True) if h1 and h1.find('span') else ''}")
        t_f = s2.find("td", class_="t_f")
        print(f"detail.has_t_f={bool(t_f)}  has_magnet={'magnet:?' in (t_f.get_text() if t_f else '')}")


if __name__ == "__main__":
    main()
