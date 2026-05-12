"""WebScraper 端到端烟雾测试：跑列表 + 详情解析，跳过数据库写入。"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scrapers.web_scraper import WebScraper  # noqa: E402


def main():
    fid = 103
    scraper = WebScraper()
    print(f"workers={scraper.workers}")

    t0 = time.time()
    info_list, tid_list = scraper._get_plate_info_batch(fid)
    print(f"\n[plate] {len(info_list)} 条 / {len(tid_list)} tid  耗时={time.time()-t0:.2f}s")
    for it in info_list[:3]:
        print(f"  {it}")

    if not info_list:
        return

    # 只取前 3 条做详情测试，避免压力
    sample = info_list[:3]
    t1 = time.time()
    details = scraper._get_thread_details_batch(sample)
    print(f"\n[detail] {len(details)} 条经过 merge+clean  耗时={time.time()-t1:.2f}s")
    for it in details:
        print(
            f"  tid={it.get('tid')}  title={it.get('title','')[:30]}  "
            f"magnet={'Y' if it.get('magnet') else 'N'}  "
            f"115={'Y' if it.get('magnet_115') else 'N'}  "
            f"img={len(it.get('img') or [])}"
        )


if __name__ == "__main__":
    main()
