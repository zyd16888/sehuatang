"""
Phase 0 探针：用 curl_cffi 抓 sehuatang 列表页和详情页样例，确认 archiver=1 简版字段够不够。

跑法：mamba run -n ame python scripts/probe_archiver.py
产出：samples/*.html + 控制台对比报告。
"""
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from curl_cffi import requests
from bs4 import BeautifulSoup

SAFEID_RE = re.compile(r"safeid\s*=\s*['\"]([^'\"]+)['\"]")

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
SAMPLES.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))

from util.read_config import get_config  # noqa: E402

DOMAIN = get_config("domain_name") or "sehuatang.org"
PROXY_CFG = get_config("proxy") or {}
PROXY = PROXY_CFG.get("proxy_url") if PROXY_CFG.get("proxy_enable") else None
PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None

UA_DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": UA_DESKTOP}
COOKIE = {"_safe": ""}


def _request(url: str) -> tuple[int, bytes]:
    r = requests.get(
        url,
        proxies=PROXIES,
        cookies=COOKIE,
        headers=HEADERS,
        allow_redirects=True,
        timeout=15,
        impersonate="chrome110",
    )
    return r.status_code, (r.content or b"")


def fetch(url: str, label: str) -> bytes | None:
    print(f"\n[{label}] GET {url}")
    t0 = time.time()
    try:
        status, body = _request(url)
    except Exception as e:
        print(f"  [ERR] 请求失败: {e}")
        return None
    dt = time.time() - t0
    print(f"  status={status}  size={len(body)}  耗时={dt:.2f}s")

    title = _title(body)
    print(f"  title={title!r}")

    if title == "不详" and b"safeid" in body:
        m = SAFEID_RE.search(body.decode("utf-8", errors="ignore"))
        if m:
            safeid = m.group(1)
            COOKIE["_safe"] = safeid
            print(f"  [BYPASS] 命中 R18，提取 safeid={safeid}，重试...")
            t1 = time.time()
            try:
                status, body = _request(url)
            except Exception as e:
                print(f"  [ERR] 重试失败: {e}")
                return None
            print(f"  status={status}  size={len(body)}  重试耗时={time.time()-t1:.2f}s")
            title = _title(body)
            print(f"  title={title!r}")

    low_title = title.lower()
    if any(k in low_title for k in ("just a moment", "attention required")) or status in (403, 429, 503):
        print("  [WARN] 命中 Cloudflare 挑战")

    out = SAMPLES / f"{label}.html"
    out.write_bytes(body)
    print(f"  saved -> {out.relative_to(ROOT)}  total={dt:.2f}s")
    return body


def _title(body: bytes) -> str:
    try:
        soup = BeautifulSoup(body, "html.parser")
        t = soup.find("title")
        if t:
            return t.get_text(strip=True)
    except Exception:
        pass
    return ""


def parse_list_normal(html: bytes) -> list[dict]:
    """普通列表页字段提取：基于现有 page_parser.py 的逻辑（id=^normalthread_ ...）"""
    soup = BeautifulSoup(html, "html.parser")
    threads = soup.find_all(id=lambda v: v and v.startswith("normalthread_"))
    rows = []
    for t in threads[:5]:
        a = t.find("a", class_="s xst")
        if not a:
            continue
        title_text = a.get_text(strip=True)
        sc = t.find(class_="showcontent y")
        tid = sc.get("id", "").split("_")[-1] if sc else ""
        date_em = t.find("td", class_="by")
        date_em = date_em.find("em") if date_em else None
        date_text = date_em.get_text(strip=True) if date_em else ""
        date_span = date_em.find("span") if date_em else None
        date_title = date_span.get("title", "") if date_span else ""
        rows.append({
            "tid": tid,
            "title_text": title_text[:60],
            "date_text": date_text,
            "date_title": date_title,
        })
    return rows


def parse_list_archiver(html: bytes) -> list[dict]:
    """archiver 列表页字段提取"""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for a in soup.find_all("a", href=True)[:200]:
        href = a["href"]
        if "viewthread" not in href and "tid=" not in href:
            continue
        q = parse_qs(urlparse(href).query)
        tid = q.get("tid", [""])[0]
        if not tid.isdigit():
            continue
        rows.append({
            "tid": tid,
            "title_text": a.get_text(strip=True)[:60],
            "href": href,
        })
        if len(rows) >= 5:
            break
    return rows


def parse_detail_normal(html: bytes) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    info = {}
    h1 = soup.find("h1", class_="ts")
    info["title"] = (h1.find("span").get_text(strip=True) if h1 and h1.find("span") else "")
    post_list = soup.find("div", id="postlist")
    t_f = post_list.find("td", class_="t_f") if post_list else soup.find("td", class_="t_f")
    info["has_t_f"] = bool(t_f)
    if t_f:
        imgs = [i.get("file") or i.get("src") for i in t_f.find_all("img")]
        info["img_count"] = len(imgs)
        info["img_sample"] = imgs[:2]
        text = t_f.get_text(" ", strip=True)
        info["has_magnet"] = "magnet:?" in text
        info["has_115"] = "115://" in text or "115.com" in text
    # post_time
    try:
        em = soup.find("img", class_="authicn vm").parent.find("em")
        span = em.find("span")
        info["post_time"] = span["title"] if span and span.has_attr("title") else em.get_text(strip=True)
    except Exception:
        info["post_time"] = None
    return info


def parse_detail_archiver(html: bytes) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    info = {}
    nav = soup.find(id="nav")
    info["nav_text"] = nav.get_text(strip=True)[:80] if nav else ""
    content = soup.find(id="content") or soup
    text = content.get_text(" ", strip=True)
    info["has_magnet_in_text"] = "magnet:?" in text
    info["has_115_in_text"] = "115://" in text or "115.com" in text
    info["has_img_bbcode"] = "[img]" in (content.decode() if hasattr(content, "decode") else str(content)).lower()
    author_p = soup.find("p", class_="author")
    info["author_text"] = author_p.get_text(strip=True)[:80] if author_p else ""
    info["content_len"] = len(text)
    return info


def main():
    fid = 103  # 高清中文字幕
    base = f"https://{DOMAIN}"

    # 桌面 UA 走普通版 URL（archiver 在 sehuatang 被 CF 直接 403，不可用）
    url_list_normal = f"{base}/forum-{fid}-1.html"
    html_list_normal = fetch(url_list_normal, "list_normal")

    print("\n" + "=" * 60)
    print("列表页字段对照")
    print("=" * 60)

    tid_for_detail = None
    if html_list_normal:
        rows = parse_list_normal(html_list_normal)
        print(f"\n[list_normal] 解析到 {len(rows)} 条:")
        for r in rows:
            print(f"  {r}")
        if rows:
            tid_for_detail = rows[0]["tid"]

    if not tid_for_detail:
        print("\n[ABORT] 无法从列表页拿到 tid，跳过详情页测试")
        return

    print(f"\n>>> 使用 tid={tid_for_detail} 测试详情页 <<<")
    time.sleep(2)

    url_det_normal = f"{base}/forum.php?mod=viewthread&tid={tid_for_detail}"
    html_det_normal = fetch(url_det_normal, "detail_normal")

    print("\n" + "=" * 60)
    print("详情页字段对照")
    print("=" * 60)

    if html_det_normal:
        info = parse_detail_normal(html_det_normal)
        print(f"\n[detail_normal] {info}")


if __name__ == "__main__":
    main()
