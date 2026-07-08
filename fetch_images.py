# -*- coding: utf-8 -*-
"""图片专载器 v2 - 修复中文URL编码+Referer"""
import os, sys, re, time, urllib.request, urllib.parse, http.cookiejar
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser

UPSTREAM = "http://dev.inkcad.com"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CONTENTS = os.path.join(DATA_DIR, "contents")
IMAGES = os.path.join(DATA_DIR, "images")

MAX_WORKERS = 2
RETRY_TIMES = 3
REFERER = UPSTREAM + '/ykyapp/App/WebBookIndex.aspx'

_cj = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj))
_opener.addheaders = [
    ('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'),
    ('Referer', REFERER),
    ('Accept', 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8'),
]


def sanitize(name):
    return (name.replace("/", "_").replace("\\", "_").replace("?", "_")
                .replace(":", "_").replace("*", "_").replace('"', "_")
                .replace("<", "_").replace(">", "_").replace("|", "_"))


def init_session():
    try:
        gal = urllib.parse.quote('机械工程师设计手册')
        _opener.open(UPSTREAM + '/ykyapp/App/WebBookIndex.aspx?gal=%s&code=202P' % gal, timeout=10).read()
        return True
    except Exception as e:
        print(f"会话初始化失败: {e}")
        return False


def resolve_img_path(src):
    """将HTML中的相对图片路径解析为标准化路径（保留中文）"""
    src = src.replace("\\", "/")
    parts = src.split("/")
    resolved = []
    for p in parts:
        if p == "..":
            if resolved:
                resolved.pop()
        elif p == "." or p == "":
            continue
        else:
            resolved.append(p)
    return "/" + "/".join(resolved)


def encode_url(std_path):
    """将带中文的URL路径编码，保留 / 分隔符"""
    # 按 / 分段编码，保留分隔符
    parts = std_path.split("/")
    encoded_parts = []
    for p in parts:
        if p:
            encoded_parts.append(urllib.parse.quote(p, safe=''))
        else:
            encoded_parts.append("")
    return "/".join(encoded_parts)


class ImgExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.srcs = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "img":
            for k, v in attrs:
                if k.lower() == "src" and v:
                    self.srcs.append(v)


def image_cached(std_path):
    """检查图片是否已缓存（按本地 sanitize 后的路径）"""
    clean = std_path.lstrip("/").replace("\\", "/")
    parts = [sanitize(p) for p in clean.split("/") if p and p != ".."]
    if not parts:
        return False
    fpath = os.path.join(IMAGES, *parts)
    return os.path.exists(fpath) and os.path.getsize(fpath) > 0


def download_image(std_path, retries=RETRY_TIMES):
    """下载单张图片并缓存"""
    if image_cached(std_path):
        return "skip"

    # 编码URL（保留中文路径作为key，但请求时编码）
    encoded = encode_url(std_path)
    url = UPSTREAM + encoded

    for attempt in range(retries):
        try:
            r = _opener.open(url, timeout=20)
            data = r.read()
            if len(data) > 50:
                # 保存：用 sanitize 后的路径
                clean = std_path.lstrip("/").replace("\\", "/")
                parts = [sanitize(p) for p in clean.split("/") if p and p != ".."]
                if not parts:
                    return "fail"
                fpath = os.path.join(IMAGES, *parts)
                os.makedirs(os.path.dirname(fpath), exist_ok=True)
                with open(fpath, "wb") as f:
                    f.write(data)
                return "ok"
            return "fail"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "missing"  # 原站不存在此图
            if attempt < retries - 1:
                time.sleep(1 + attempt)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1 + attempt)
            else:
                return "fail"
    return "fail"


def collect_image_urls():
    """扫描所有内容页，提取唯一图片URL"""
    print("扫描内容页...")
    urls = set()
    scanned = 0
    for root, dirs, files in os.walk(CONTENTS):
        for fname in files:
            if not fname.endswith(".html"):
                continue
            try:
                with open(os.path.join(root, fname), "rb") as f:
                    raw = f.read()
                html = raw.decode("utf-8", errors="replace")
                parser = ImgExtractor()
                parser.feed(html)
                for src in parser.srcs:
                    if src and not src.startswith(("data:", "http://", "https://")):
                        urls.add(resolve_img_path(src))
                scanned += 1
                if scanned % 2000 == 0:
                    sys.stdout.write(f"\r  已扫描 {scanned} 页, {len(urls)} 个唯一图片")
                    sys.stdout.flush()
            except Exception:
                pass
    print(f"\r  扫描完成: {scanned} 页, {len(urls)} 个唯一图片URL")
    return urls


def main():
    print("=" * 50)
    print("  图片专载器 v2 - 2线程")
    print("=" * 50)

    # 检测原站
    print("初始化会话...")
    if not init_session():
        print("原站不可用，退出")
        return
    print("原站可用")

    # 收集URL
    urls = collect_image_urls()
    if not urls:
        print("未找到图片")
        return

    # 过滤已缓存
    to_download = [u for u in urls if not image_cached(u)]
    cached = len(urls) - len(to_download)
    print(f"  待下载: {len(to_download)}, 已缓存: {cached}")

    if not to_download:
        print("全部已缓存！")
        return

    stats = {"ok": 0, "fail": 0, "skip": 0, "missing": 0}
    done = 0
    total = len(to_download)
    start_time = time.time()
    last_session_refresh = 0

    def worker(url):
        return download_image(url)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(worker, u): u for u in to_download}
        for future in as_completed(futures):
            done += 1
            result = future.result()
            stats[result] = stats.get(result, 0) + 1

            # 连续失败太多时刷新会话
            if stats['fail'] > 0 and stats['fail'] % 50 == 0 and time.time() - last_session_refresh > 60:
                init_session()
                last_session_refresh = time.time()

            # 每500张打印一次进度
            if done % 500 == 0 or done == total:
                elapsed = time.time() - start_time
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                sys.stdout.write(
                    f"\r  {done}/{total} (成功{stats['ok']} 失败{stats['fail']} 跳过{stats['skip']} 缺失{stats.get('missing',0)}) "
                    f"速度:{rate:.1f}/s 剩余:{eta/60:.1f}分钟"
                )
                sys.stdout.flush()
    print()

    # 报告
    final = sum(len(files) for r, d, files in os.walk(IMAGES))
    total_mb = sum(os.path.getsize(os.path.join(r, f)) for r, d, files in os.walk(IMAGES) for f in files) / 1024 / 1024
    print(f"\n图片总计: {final} 个, {total_mb:.1f} MB")
    print(f"本次: 成功{stats['ok']} 失败{stats['fail']} 跳过{stats['skip']} 缺失{stats.get('missing',0)}")


if __name__ == "__main__":
    main()
