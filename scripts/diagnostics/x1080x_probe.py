"""只读探测 x1080x 镜像站：连通性、CF 挑战、板块结构。不写数据库。"""
import os
import re
import sys

from curl_cffi import requests

DOMAINS = ["www.x666x.me", "x999x.me", "x222x.me", "x000.me"]
PROXY = os.getenv("X1080X_PROBE_PROXY") or None
IMPERSONATE = os.getenv("X1080X_PROBE_IMPERSONATE", "chrome110")


def probe(domain: str) -> None:
    url = f"https://{domain}/forum.php"
    try:
        r = requests.get(
            url,
            impersonate=IMPERSONATE,
            timeout=30,
            proxies={"http": PROXY, "https": PROXY} if PROXY else None,
        )
    except Exception as e:  # noqa: BLE001 手工诊断脚本，直接打印
        print(f"{domain}: ERROR {e}")
        return

    body = r.text
    if "Just a moment" in body or "challenges.cloudflare.com" in body:
        print(f"{domain}: HTTP {r.status_code}, Cloudflare challenge")
        return

    title = re.search(r"<title>(.*?)</title>", body)
    print(f"{domain}: HTTP {r.status_code}, {len(body)} bytes, title={title.group(1) if title else '?'}")

    forums = re.findall(
        r'href="(forum\.php\?mod=forumdisplay&(?:amp;)?fid=(\d+)[^"]*|forum-(\d+)-1\.html)"[^>]*>([^<]{2,40})</a>',
        body,
    )
    seen = set()
    for href, fid1, fid2, name in forums[:40]:
        fid = fid1 or fid2
        if fid and fid not in seen:
            seen.add(fid)
            print(f"  fid={fid}  {name.strip()}  ({href})")
    if not seen:
        print("  未解析到板块链接，打印正文片段辅助判断：")
        print(body[:1500])


if __name__ == "__main__":
    for domain in sys.argv[1:] or DOMAINS:
        probe(domain)
        print("-" * 60)
