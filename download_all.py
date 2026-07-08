# -*- coding: utf-8 -*-
"""
离线下载器 v3 - 直连原站（不走代理）
- 自带 cookie/session 管理
- BFS 分层并发下载子节点
- 内容页并发下载并直接缓存
- 自动提取并下载内容页中的图片
- 断点续传：跳过已缓存
- 重试机制：502/超时自动重试 3 次
- 完整性报告
不依赖 server.py，直接访问 dev.inkcad.com
"""
import os, sys, json, time, re, threading, subprocess, tempfile
import urllib.request, urllib.parse, urllib.error
import http.cookiejar
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser

UPSTREAM = "http://dev.inkcad.com"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MAX_WORKERS = 2
RETRY_TIMES = 3
RETRY_INTERVAL = 5

# 全局统计
stats_lock = threading.Lock()
stats = {"children_ok": 0, "children_fail": 0, "children_leaf": 0,
         "content_ok": 0, "content_fail": 0, "content_skip": 0,
         "image_ok": 0, "image_fail": 0, "image_skip": 0,
         "tree_ok": 0, "tree_fail": 0}

# 共享 opener（带 cookie）
_cj = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj))
_opener.addheaders = [
    ('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'),
    ('Accept', '*/*'),
    ('Referer', UPSTREAM + '/ykyapp/App/WebBookIndex.aspx'),
]


def sanitize(name):
    return name.replace("/", "_").replace("\\", "_").replace("?", "_").replace(":", "_") \
               .replace("*", "_").replace('"', "_").replace("<", "_").replace(">", "_").replace("|", "_")


def load_json(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return None
    return None


def is_children_cached(book_path, parent_id):
    cpath = os.path.join(DATA_DIR, "children", sanitize(book_path), f"{parent_id}.json")
    return os.path.exists(cpath) and os.path.getsize(cpath) > 10


def is_content_cached(book_path, itempath, itemcontent):
    cdir = os.path.join(DATA_DIR, "contents", sanitize(book_path))
    fname = sanitize(itempath + "_" + itemcontent) + ".html"
    fpath = os.path.join(cdir, fname)
    return os.path.exists(fpath) and os.path.getsize(fpath) > 500


# ===== 缓存函数（与 server.py 一致） =====

def save_local_children(book_path, parent_id, children):
    safe = sanitize(book_path)
    cdir = os.path.join(DATA_DIR, "children", safe)
    os.makedirs(cdir, exist_ok=True)
    fpath = os.path.join(cdir, f"{parent_id}.json")
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(children, f, ensure_ascii=False, indent=2)


def save_local_content(book_path, itempath, itemcontent, data):
    """保存内容页，自动裁剪 ASP.NET 垃圾"""
    safe_book = sanitize(book_path)
    fname = sanitize(itempath + "_" + itemcontent) + ".html"
    content_dir = os.path.join(DATA_DIR, "contents", safe_book)
    os.makedirs(content_dir, exist_ok=True)
    fpath = os.path.join(content_dir, fname)
    try:
        # 裁剪 ASP.NET 包装，只保留 HtmContent 内的工程内容
        html = data.decode("gb2312", errors="replace")
        m = re.search(r'<div\s+id\s*=\s*["\']?\s*HtmContent\s*["\']?\s*[^>]*>', html, re.IGNORECASE)
        if m:
            tail = html[m.end():]
            me = re.search(r'</div>\s*\n\s*<input\s+type\s*=\s*["\']hidden["\']\s+name\s*=\s*["\']hidRoot["\']', tail, re.IGNORECASE)
            if me:
                core = tail[:me.start()].strip()
                if len(core) > 100:
                    core = re.sub(r'charset\s*=\s*gb2312', 'charset=utf-8', core, count=1, flags=re.IGNORECASE)
                    data = core.encode("utf-8")
        with open(fpath, "wb") as f:
            f.write(data)
    except:
        pass


def save_local_image(img_path, data):
    clean = img_path.lstrip("/").replace("\\", "/")
    parts = [sanitize(p) for p in clean.split("/") if p and p != ".."]
    if not parts:
        return
    fpath = os.path.join(DATA_DIR, "images", *parts)
    img_dir = os.path.dirname(fpath)
    os.makedirs(img_dir, exist_ok=True)
    try:
        with open(fpath, "wb") as f:
            f.write(data)
    except:
        pass


def get_local_image_path(img_path):
    clean = img_path.lstrip("/").replace("\\", "/")
    parts = [sanitize(p) for p in clean.split("/") if p and p != ".."]
    if not parts:
        return None
    fpath = os.path.join(DATA_DIR, "images", *parts)
    if os.path.exists(fpath) and os.path.getsize(fpath) > 100:
        return fpath
    return None


# ===== Node.js 解析 =====

def execute_js(script_code, var_name):
    wrap = script_code + "\nconsole.log(JSON.stringify(" + var_name + "));\n"
    tf = tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False, encoding='utf-8')
    try:
        tf.write(wrap)
        tf.close()
        result = subprocess.run(["node", tf.name], capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
    except:
        pass
    finally:
        try:
            os.unlink(tf.name)
        except:
            pass
    return None


# ===== 原站连通性 =====

def init_session():
    """访问页面获取 session cookie"""
    try:
        gal = urllib.parse.quote('机械工程师设计手册')
        url = UPSTREAM + '/ykyapp/App/WebBookIndex.aspx?gal=%s&code=202P' % gal
        r = _opener.open(url, timeout=15)
        r.read()
        return True
    except:
        return False


def check_upstream():
    """检测原站是否可用"""
    try:
        paras = {'varList': '_templetList', 'subPath': 'Book', 'userId': '0'}
        body = urllib.parse.urlencode({'callType': 'GetGalList', 'callParas': json.dumps(paras)}).encode('utf-8')
        req = urllib.request.Request(UPSTREAM + '/ykyapp/App/ykyApp.ashx', data=body, method='POST')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded; charset=UTF-8')
        r = _opener.open(req, timeout=20)
        text = r.read().decode('utf-8', errors='replace')
        return text.strip().startswith('var retJson'), text
    except urllib.error.HTTPError as e:
        return False, 'HTTP %d' % e.code
    except Exception as e:
        return False, str(e)[:60]


# ===== API 调用（直连原站，带重试） =====

def call_api_direct(call_type, paras, retries=RETRY_TIMES):
    """直连原站 API，返回解析后的 (retJson, nodes) 或 None"""
    paras_json = json.dumps(paras, ensure_ascii=False)
    body = urllib.parse.urlencode({'callType': call_type, 'callParas': paras_json}).encode('utf-8')

    for attempt in range(retries):
        try:
            req = urllib.request.Request(UPSTREAM + '/ykyapp/App/ykyApp.ashx', data=body, method='POST')
            req.add_header('Content-Type', 'application/x-www-form-urlencoded; charset=UTF-8')
            resp = _opener.open(req, timeout=30)
            text = resp.read().decode('utf-8', errors='replace')

            if not text.strip().startswith('var retJson'):
                if attempt < retries - 1:
                    time.sleep(RETRY_INTERVAL)
                    continue
                return None

            match = re.search(r'var retJson=(\{.*?\});', text, re.DOTALL)
            if not match:
                return None
            ret = json.loads(match.group(1))
            sc = ret.get('scriptCode', '')
            nodes = None
            if '_templetData' in sc:
                nodes = execute_js(sc, '_templetData')
            elif '_templetList' in sc:
                nodes = execute_js(sc, '_templetList')
            return (ret, nodes)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(RETRY_INTERVAL)
            else:
                return None
    return None


def call_child_api(book_path, pid, retries=RETRY_TIMES):
    """调用 GetChildDrawingDir，返回 nodes 列表（可能为空）或 None（失败）"""
    paras = {
        'galPath': book_path, 'subPath': 'Book', 'varData': '_templetData',
        'parentId': int(pid) if str(pid).isdigit() else pid, 'userId': '0',
    }
    result = call_api_direct('GetChildDrawingDir', paras, retries)
    if result is None:
        return None
    ret, nodes = result
    if nodes is None:
        return []
    # 缓存
    save_local_children(book_path, pid, nodes)
    return nodes


_session_fail_count = 0

def fetch_content_direct(book_path, itempath, itemcontent, retries=RETRY_TIMES):
    """直连原站下载内容页并缓存（自动刷新 session）"""
    global _session_fail_count
    if is_content_cached(book_path, itempath, itemcontent):
        return True
    url = (UPSTREAM + '/ykyapp/App/ManualContentPage.aspx'
           f'?gal={urllib.parse.quote(book_path)}'
           f'&path={urllib.parse.quote(itempath)}'
           f'&content={urllib.parse.quote(itemcontent)}')
    for attempt in range(retries):
        try:
            r = _opener.open(url, timeout=30)
            data = r.read()
            if len(data) > 500:
                save_local_content(book_path, itempath, itemcontent, data)
                _session_fail_count = 0
                return True
            if attempt < retries - 1:
                _session_fail_count += 1
                if _session_fail_count > 5:
                    init_session()
                    _session_fail_count = 0
                time.sleep(RETRY_INTERVAL)
        except:
            if attempt < retries - 1:
                _session_fail_count += 1
                if _session_fail_count > 5:
                    init_session()
                    _session_fail_count = 0
                time.sleep(RETRY_INTERVAL)
    return False


def fetch_image_direct(img_path, retries=2):
    """直连原站下载图片并缓存"""
    if not img_path or img_path.startswith(('data:', 'http://', 'https://')):
        return False
    if not img_path.startswith('/'):
        img_path = '/' + img_path
    # 检查本地缓存
    if get_local_image_path(img_path):
        return True
    url = UPSTREAM + img_path
    for attempt in range(retries):
        try:
            r = _opener.open(url, timeout=15)
            data = r.read()
            if len(data) > 100:
                save_local_image(img_path, data)
                return True
            return False
        except:
            if attempt < retries - 1:
                time.sleep(2)
    return False


# ===== HTML 图片提取 =====

class ImgExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.srcs = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'img':
            for k, v in attrs:
                if k.lower() == 'src' and v:
                    self.srcs.append(v)


def extract_images_from_html(html_bytes):
    try:
        html = html_bytes.decode('utf-8', errors='replace')
    except:
        return []
    parser = ImgExtractor()
    try:
        parser.feed(html)
    except:
        pass
    return parser.srcs


# ===== 节点收集 =====

def collect_all_nodes(book_path, nodes):
    result = []
    visited = set()

    def walk(node_list):
        for node in node_list:
            node_id = node.get('id', '')
            if node_id in visited:
                continue
            visited.add(node_id)
            if node.get('itempath') and node.get('itemcontent'):
                result.append({
                    'id': node_id,
                    'text': node.get('text', ''),
                    'itempath': node['itempath'],
                    'itemcontent': node['itemcontent'],
                })
            pid = node_id[1:] if node_id.startswith('F') else node_id
            children = load_json(os.path.join(DATA_DIR, 'children', sanitize(book_path), f'{pid}.json'))
            if children:
                walk(children)
            elif node.get('children'):
                walk(node['children'])

    walk(nodes)
    return result


# ===== 下载流程 =====

def download_tree(book_path, book_name):
    """下载一本书的目录树"""
    safe = sanitize(book_path)
    tree_path = os.path.join(DATA_DIR, 'trees', f'{safe}.json')
    if load_json(tree_path):
        return True

    print(f'  下载目录树: {book_name}')
    paras = {'galPath': book_path, 'subPath': 'Book', 'varData': '_templetData',
             'showClass': '0', 'userId': '0'}
    result = call_api_direct('GetDrawingDir', paras)
    if result is None:
        with stats_lock:
            stats['tree_fail'] += 1
        print(f'    失败')
        return False

    ret, nodes = result
    if nodes:
        os.makedirs(os.path.join(DATA_DIR, 'trees'), exist_ok=True)
        with open(tree_path, 'w', encoding='utf-8') as f:
            json.dump(nodes, f, ensure_ascii=False, indent=2)
        with stats_lock:
            stats['tree_ok'] += 1
        print(f'    已保存 {len(nodes)} 个顶层节点')
        return True
    with stats_lock:
        stats['tree_fail'] += 1
    print(f'    空数据')
    return False


def download_children_bfs(book_path, tree):
    """BFS 分层并发下载所有子节点"""
    visited = set()
    queue = []

    def enqueue(nodes):
        for node in nodes:
            node_id = node.get('id', '')
            pid = node_id[1:] if node_id.startswith('F') else node_id
            if pid not in visited:
                visited.add(pid)
                queue.append((node_id, pid))

    enqueue(tree)
    round_num = 0

    while queue:
        round_num += 1
        current_batch = queue[:]
        queue.clear()

        to_download = [(nid, pid) for nid, pid in current_batch if not is_children_cached(book_path, pid)]
        already = len(current_batch) - len(to_download)

        for nid, pid in current_batch:
            if is_children_cached(book_path, pid):
                children = load_json(os.path.join(DATA_DIR, 'children', sanitize(book_path), f'{pid}.json'))
                if children:
                    enqueue(children)

        if not to_download:
            continue

        print(f'  第 {round_num} 轮: {len(current_batch)} 节点 ({len(to_download)} 待下载, {already} 已缓存)')

        def worker(task):
            nid, pid = task
            return (pid, call_child_api(book_path, pid))

        done = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(worker, t): t for t in to_download}
            for future in as_completed(futures):
                done += 1
                pid, nodes = future.result()
                with stats_lock:
                    if nodes is None:
                        stats['children_fail'] += 1
                    elif len(nodes) == 0:
                        stats['children_leaf'] += 1
                    else:
                        stats['children_ok'] += 1
                        enqueue(nodes)
                ok = stats['children_ok'] + stats['children_leaf']
                fail = stats['children_fail']
                sys.stdout.write(f'\r    {done}/{len(to_download)} (成功{ok} 失败{fail})')
                sys.stdout.flush()
        print()


def download_contents(book_path, nodes):
    """并发下载所有内容页"""
    to_download = [(n['itempath'], n['itemcontent']) for n in nodes
                   if not is_content_cached(book_path, n['itempath'], n['itemcontent'])]
    cached = len(nodes) - len(to_download)
    print(f'  内容页: {len(nodes)} 个 ({len(to_download)} 待下载, {cached} 已缓存)')

    if not to_download:
        with stats_lock:
            stats['content_skip'] += cached
        return

    def worker(task):
        ipath, icontent = task
        return fetch_content_direct(book_path, ipath, icontent)

    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(worker, t): t for t in to_download}
        for future in as_completed(futures):
            done += 1
            ok = future.result()
            with stats_lock:
                if ok:
                    stats['content_ok'] += 1
                else:
                    stats['content_fail'] += 1
            sys.stdout.write(f'\r    {done}/{len(to_download)} (成功{stats["content_ok"]} 失败{stats["content_fail"]})')
            sys.stdout.flush()
    print()


def download_images_for_book(book_path, nodes):
    """扫描已缓存内容页，提取并下载图片"""
    print(f'  图片: 扫描内容页提取 URL...')
    img_urls = set()
    scanned = 0
    for n in nodes:
        cdir = os.path.join(DATA_DIR, 'contents', sanitize(book_path))
        fname = sanitize(n['itempath'] + '_' + n['itemcontent']) + '.html'
        fpath = os.path.join(cdir, fname)
        if os.path.exists(fpath) and os.path.getsize(fpath) > 500:
            try:
                with open(fpath, 'rb') as f:
                    html = f.read()
                for src in extract_images_from_html(html):
                    img_urls.add(src)
                scanned += 1
            except:
                pass

    img_urls = {u for u in img_urls if u and not u.startswith(('data:', 'http://', 'https://'))}
    print(f'  图片: 扫描 {scanned} 页, 提取 {len(img_urls)} 个唯一图片 URL')

    if not img_urls:
        return

    to_download = [u for u in img_urls if not get_local_image_path(u)]
    cached = len(img_urls) - len(to_download)
    print(f'  图片: {len(img_urls)} 个 ({len(to_download)} 待下载, {cached} 已缓存)')

    if not to_download:
        with stats_lock:
            stats['image_skip'] += cached
        return

    def worker(url):
        return fetch_image_direct(url)

    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(worker, u): u for u in to_download}
        for future in as_completed(futures):
            done += 1
            ok = future.result()
            with stats_lock:
                if ok:
                    stats['image_ok'] += 1
                else:
                    stats['image_fail'] += 1
            sys.stdout.write(f'\r    {done}/{len(to_download)} (成功{stats["image_ok"]} 失败{stats["image_fail"]})')
            sys.stdout.flush()
    print()


def integrity_report(books):
    """完整性报告"""
    print('\n' + '=' * 60)
    print('  数据完整性报告')
    print('=' * 60)
    total_nodes = 0
    total_contents = 0
    for book in books:
        path = book['path']
        safe = sanitize(path)
        tree_path = os.path.join(DATA_DIR, 'trees', f'{safe}.json')
        tree = load_json(tree_path)
        if not tree:
            print(f'\n  [{book["text"]}]')
            print(f'    状态: 目录树未下载')
            continue
        nodes = collect_all_nodes(path, tree)
        children_dir = os.path.join(DATA_DIR, 'children', safe)
        children_count = len(os.listdir(children_dir)) if os.path.exists(children_dir) else 0
        content_dir = os.path.join(DATA_DIR, 'contents', safe)
        content_count = 0
        if os.path.exists(content_dir):
            content_count = len([f for f in os.listdir(content_dir) if f.endswith('.html')])
        print(f'\n  [{book["text"]}]')
        print(f'    目录树节点: {len(nodes)} 个')
        print(f'    子节点缓存: {children_count} 个文件')
        print(f'    内容页缓存: {content_count} / {len(nodes)} 页')
        coverage = (content_count / len(nodes) * 100) if nodes else 0
        print(f'    内容覆盖率: {coverage:.1f}%')
        total_nodes += len(nodes)
        total_contents += content_count
    print('\n' + '-' * 60)
    print(f'  总计: {total_nodes} 节点, {total_contents} 内容页')
    img_count = 0
    img_dir = os.path.join(DATA_DIR, 'images')
    if os.path.exists(img_dir):
        for root, dirs, files in os.walk(img_dir):
            img_count += len(files)
    print(f'  图片: {img_count} 个')
    print('=' * 60)


def main():
    print('=' * 60)
    print('  离线下载器 v3 - 直连原站（不走代理）')
    print('=' * 60)
    print(f'  原站: {UPSTREAM}')
    print(f'  并发数: {MAX_WORKERS}, 重试: {RETRY_TIMES} 次')
    print('=' * 60)

    books = load_json(os.path.join(DATA_DIR, 'galList.json'))
    if not books:
        print('错误: data/galList.json 不存在，请先运行 init_data.py')
        return

    # 跳过电气工程师（数据量大，用户要求跳过）
    books = [b for b in books if '电气' not in b.get('text', '') and 'ManualEL' not in b.get('path', '')]
    print(f'  下载范围: {len(books)} 本 ({", ".join(b["text"] for b in books)})')

    # 初始化 session
    print('\n初始化 session...')
    init_session()

    # 检测原站
    print('检测原站连通性...')
    ok, desc = check_upstream()
    if ok:
        print('  原站可用，开始全量下载')
    else:
        print(f'  原站不可用 ({desc})')
        print('  提示: 原站恢复后再次运行即可下载缺失数据')
        integrity_report(books)
        return

    # [1/4] 下载目录树
    print('\n[1/4] 检查目录树...')
    for book in books:
        download_tree(book['path'], book['text'])

    # [2/4] 下载子节点
    print('\n[2/4] 下载子节点（BFS 分层并发）...')
    for book in books:
        path = book['path']
        tree = load_json(os.path.join(DATA_DIR, 'trees', f'{sanitize(path)}.json'))
        if not tree:
            continue
        print(f'\n  《{book["text"]}》')
        download_children_bfs(path, tree)

    # [3/4] 下载内容页
    print('\n[3/4] 下载内容页...')
    for book in books:
        path = book['path']
        tree = load_json(os.path.join(DATA_DIR, 'trees', f'{sanitize(path)}.json'))
        if not tree:
            continue
        nodes = collect_all_nodes(path, tree)
        if not nodes:
            continue
        print(f'\n  《{book["text"]}》 ({len(nodes)} 节点)')
        download_contents(path, nodes)

    # [4/4] 下载图片
    print('\n[4/4] 下载图片...')
    for book in books:
        path = book['path']
        tree = load_json(os.path.join(DATA_DIR, 'trees', f'{sanitize(path)}.json'))
        if not tree:
            continue
        nodes = collect_all_nodes(path, tree)
        if not nodes:
            continue
        print(f'\n  《{book["text"]}》')
        download_images_for_book(path, nodes)

    # 最终报告
    print('\n' + '=' * 60)
    print('  下载统计')
    print('=' * 60)
    print(f'  目录树: 成功 {stats["tree_ok"]}, 失败 {stats["tree_fail"]}')
    print(f'  子节点: 成功 {stats["children_ok"]}, 叶子 {stats["children_leaf"]}, 失败 {stats["children_fail"]}')
    print(f'  内容页: 成功 {stats["content_ok"]}, 失败 {stats["content_fail"]}, 跳过 {stats["content_skip"]}')
    print(f'  图片:   成功 {stats["image_ok"]}, 失败 {stats["image_fail"]}, 跳过 {stats["image_skip"]}')

    integrity_report(books)


if __name__ == '__main__':
    main()
