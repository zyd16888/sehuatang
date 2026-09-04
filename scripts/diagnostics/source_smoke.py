"""只读抓取少量 Sehuatang 列表与详情，验证解析链路。"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scrapers.web_scraper import WebScraper  # noqa: E402


def main():
    with WebScraper(dry_run=True) as scraper:
        started = time.monotonic()
        info_list, tid_list = scraper._get_plate_info_batch(103)
        print(
            f"plate: items={len(info_list)} tids={len(tid_list)} "
            f"elapsed={time.monotonic()-started:.2f}s"
        )
        sample = info_list[:3]
        if not sample:
            return
        started = time.monotonic()
        details = scraper._get_thread_details_batch(sample)
        print(
            f"detail: valid={len(details)} "
            f"elapsed={time.monotonic()-started:.2f}s"
        )


if __name__ == "__main__":
    main()
