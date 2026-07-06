# -*- coding: utf-8 -*-
"""
工程手册服务器
- 优先从本地 data/ 目录读取缓存数据
- 本地无缓存时回退代理到 dev.inkcad.com
- 源站关闭时，只要数据已下载，仍可独立运行
"""

import os
import re
import sys
import json
import time
import threading
import subprocess
import tempfile
import urllib.request
import urllib.parse
import urllib.error
import http.server
import socketserver
from http.cookiejar import CookieJar

# ===== 配置 =====
PORT = 8000
LOCAL_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(LOCAL_DIR, "data")
UPSTREAM = "http://dev.inkcad.com"
OFFLINE_MODE = False  # True=纯本地模式（不访问源站），False=代理回退模式

# 本地前端文件
LOCAL_FILES = {"/", "/index.html", "/app.js", "/style.css", "/favicon.ico"}

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf",
    ".eot": "application/vnd.ms-fontobject",
}

# ===== 本地数据缓存帮助函数 =====

def sanitize_filename(name):
    return name.replace("/", "_").replace("\\", "_").replace("?", "_").replace(":", "_") \
               .replace("*", "_").replace('"', "_").replace("<", "_").replace(">", "_").replace("|", "_")

def load_local_json(filepath):
    """加载本地 JSON 文件"""
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def get_local_content(book_path, itempath, itemcontent):
    """获取本地缓存的内容页"""
    safe_book = sanitize_filename(book_path)
    fname = sanitize_filename(itempath + "_" + itemcontent) + ".html"
    fpath = os.path.join(DATA_DIR, "contents", safe_book, fname)
    if os.path.exists(fpath) and os.path.getsize(fpath) > 500:
        with open(fpath, "rb") as f:
            return f.read()
    return None

def get_local_tree(book_path):
    """获取本地缓存的目录树"""
    safe = sanitize_filename(book_path)
    fpath = os.path.join(DATA_DIR, "trees", f"{safe}.json")
    return load_local_json(fpath)

def get_local_children(book_path, parent_id):
    """获取本地缓存的子节点"""
    safe = sanitize_filename(book_path)
    cdir = os.path.join(DATA_DIR, "children", safe)
    fpath = os.path.join(cdir, f"{parent_id}.json")
    return load_local_json(fpath)

def save_local_children(book_path, parent_id, children):
    """保存子节点到本地缓存"""
    safe = sanitize_filename(book_path)
    cdir = os.path.join(DATA_DIR, "children", safe)
    os.makedirs(cdir, exist_ok=True)
    fpath = os.path.join(cdir, f"{parent_id}.json")
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(children, f, ensure_ascii=False, indent=2)

def save_local_content(book_path, itempath, itemcontent, data):
    """保存内容页到本地缓存"""
    safe_book = sanitize_filename(book_path)
    fname = sanitize_filename(itempath + "_" + itemcontent) + ".html"
    content_dir = os.path.join(DATA_DIR, "contents", safe_book)
    os.makedirs(content_dir, exist_ok=True)
    fpath = os.path.join(content_dir, fname)
    try:
        with open(fpath, "wb") as f:
            f.write(data)
    except:
        pass


def get_local_image(img_path):
    """获取本地缓存的图片"""
    fpath = os.path.join(DATA_DIR, "images", *split_and_sanitize_path(img_path))
    if os.path.exists(fpath) and os.path.getsize(fpath) > 100:
        return fpath
    return None


def save_local_image(img_path, data):
    """保存图片到本地缓存，保留目录结构"""
    parts = split_and_sanitize_path(img_path)
    fpath = os.path.join(DATA_DIR, "images", *parts)
    img_dir = os.path.dirname(fpath)
    os.makedirs(img_dir, exist_ok=True)
    try:
        with open(fpath, "wb") as f:
            f.write(data)
    except:
        pass


def split_and_sanitize_path(path_str):
    """按路径分隔符拆分并逐个 sanitize，保留目录结构"""
    # 去掉开头 / 和 ../
    clean = path_str.lstrip("/").replace("\\", "/")
    parts = clean.split("/")
    result = []
    for p in parts:
        if p == ".." or not p:
            continue
        result.append(sanitize_filename(p))
    return result


def save_api_response(paras, response_data):
    """保存 API 响应到本地缓存"""
    gal_path = paras.get("galPath", "")
    if not gal_path:
        return
    safe = sanitize_filename(gal_path)
    os.makedirs(os.path.join(DATA_DIR, "trees"), exist_ok=True)

    # 解析响应数据
    try:
        text = response_data.decode("utf-8")
        match = re.search(r"var retJson=(\{.*?\});", text, re.DOTALL)
        if not match:
            return
        ret = json.loads(match.group(1))
        sc = ret.get("scriptCode", "")

        if "_templetData" in sc:
            # 用 Node.js 解析
            nodes = execute_js_data(sc, "_templetData")
            if nodes:
                tree_path = os.path.join(DATA_DIR, "trees", f"{safe}.json")
                with open(tree_path, "w", encoding="utf-8") as f:
                    json.dump(nodes, f, ensure_ascii=False, indent=2)
    except:
        pass


def execute_js_data(script_code, var_name):
    """用 Node.js 解析 JS 数据"""
    import subprocess, tempfile
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


def make_api_response(data, var_name="_templetData"):
    """构造与原站格式一致的 API 响应"""
    script_code = f"var {var_name}=" + json.dumps(data, ensure_ascii=False) + ";"
    ret = {"result": "0", "scriptCode": script_code, "err": ""}
    return ("var retJson=" + json.dumps(ret, ensure_ascii=False) + ";").encode("utf-8")


class RequestHandler(http.server.BaseHTTPRequestHandler):
    cookie_jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    # 后台预取线程池
    _prefetch_lock = threading.Lock()
    _prefetch_queue = set()

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # ===== 发送响应 =====
    def send_ok(self, content, content_type="text/html; charset=utf-8"):
        if isinstance(content, str):
            content = content.encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def serve_local_file(self, file_path):
        # 如果是绝对路径就直接用，否则相对于 LOCAL_DIR
        if os.path.isabs(file_path) and os.path.exists(file_path):
            full = file_path
        else:
            full = os.path.join(LOCAL_DIR, file_path.lstrip("/"))
        if not os.path.exists(full) or os.path.isdir(full):
            self.send_error(404, "Not found")
            return
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            content = f.read()
        self.send_ok(content, MIME_TYPES.get(ext, "application/octet-stream"))

    # ===== 代理上游 =====
    def proxy_upstream(self, upstream_path, method="GET", body=None, extra_headers=None,
                       save_content_key=None):
        if OFFLINE_MODE:
            self.send_error(503, "Offline mode - data not cached")
            return
        url = UPSTREAM + upstream_path
        req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Referer": UPSTREAM + "/ykyapp/App/WebBookIndex.aspx",
        }
        if extra_headers:
            req_headers.update(extra_headers)

        req = urllib.request.Request(url, data=body, method=method, headers=req_headers)

        try:
            resp = self.opener.open(req, timeout=30)
            content = resp.read()
            resp_headers = dict(resp.headers)

            # 写透缓存：如果是内容页，保存到本地
            if save_content_key:
                save_local_content(*save_content_key, content)

            self.send_response(resp.status)
            for k in ["Content-Type", "Content-Length"]:
                if k in resp_headers:
                    self.send_header(k, resp_headers[k])
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if e.fp:
                self.wfile.write(e.read())
        except Exception as e:
            err_msg = str(e).encode("ascii", errors="replace").decode("ascii")
            try:
                self.send_error(502, "Upstream error: %s" % err_msg)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass

    def proxy_upstream_and_cache_children(self, upstream_path, gal_path, parent_id, body, content_type):
        """代理 GetChildDrawingDir 请求并缓存结果"""
        if OFFLINE_MODE:
            try:
                self.send_error(503, "Offline mode - children not cached")
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass
            return
        url = UPSTREAM + upstream_path
        req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Referer": UPSTREAM + "/ykyapp/App/WebBookIndex.aspx",
            "Content-Type": content_type,
        }
        req = urllib.request.Request(url, data=body, method="POST", headers=req_headers)
        try:
            resp = self.opener.open(req, timeout=30)
            content = resp.read()
            resp_headers = dict(resp.headers)

            # 解析并缓存子节点数据
            try:
                text = content.decode("utf-8")
                match = re.search(r"var retJson=(\{.*?\});", text, re.DOTALL)
                if match:
                    ret = json.loads(match.group(1))
                    sc = ret.get("scriptCode", "")
                    if "_templetData" in sc:
                        nodes = execute_js_data(sc, "_templetData")
                        if nodes:
                            save_local_children(gal_path, parent_id, nodes)
                            # 后台预取更深层子节点
                            prefetch_deep_children(gal_path, nodes)
            except:
                pass

            self.send_response(resp.status)
            for k in ["Content-Type", "Content-Length"]:
                if k in resp_headers:
                    self.send_header(k, resp_headers[k])
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if e.fp:
                self.wfile.write(e.read())
        except Exception as e:
            err_msg = str(e).encode("ascii", errors="replace").decode("ascii")
            try:
                self.send_error(502, "Upstream error: %s" % err_msg)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass

    def proxy_upstream_and_cache_image(self, upstream_path, img_key):
        """代理图片/CSS 请求并缓存"""
        if OFFLINE_MODE:
            self.send_error(503, "Offline mode - image/CSS not cached")
            return
        url = UPSTREAM + upstream_path
        req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Referer": UPSTREAM + "/ykyapp/App/WebBookIndex.aspx",
        }
        req = urllib.request.Request(url, method="GET", headers=req_headers)
        try:
            resp = self.opener.open(req, timeout=30)
            content = resp.read()
            resp_headers = dict(resp.headers)

            # 缓存图片
            if len(content) > 100:
                save_local_image(img_key, content)

            self.send_response(resp.status)
            for k in ["Content-Type", "Content-Length"]:
                if k in resp_headers:
                    self.send_header(k, resp_headers[k])
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if e.fp:
                self.wfile.write(e.read())
        except Exception as e:
            err_msg = str(e).encode("ascii", errors="replace").decode("ascii")
            self.send_error(502, "Upstream error: %s" % err_msg)

    # ===== API 解析 =====
    def parse_api_body(self, body):
        """解析 POST body 中的 callType 和 callParas"""
        params = urllib.parse.parse_qs(body.decode("utf-8") if isinstance(body, bytes) else body)
        call_type = params.get("callType", [""])[0]
        call_paras_str = params.get("callParas", ["{}"])[0]
        try:
            paras = json.loads(call_paras_str)
        except:
            paras = {}
        return call_type, paras

    # ===== 处理本地 API 请求 =====
    def handle_local_api(self, call_type, paras):
        """尝试从本地数据响应 API 请求，返回 True 表示已处理"""
        try:
            if call_type == "GetGalList":
                data = load_local_json(os.path.join(DATA_DIR, "galList.json"))
                if data:
                    self.send_ok(make_api_response(data, "_templetList"))
                    return True

            elif call_type == "GetDrawingDir":
                gal_path = paras.get("galPath", "")
                data = get_local_tree(gal_path)
                if data:
                    self.send_ok(make_api_response(data, "_templetData"))
                    return True

            elif call_type == "GetChildDrawingDir":
                gal_path = paras.get("galPath", "")
                parent_id = str(paras.get("parentId", ""))
                # 1. 先从本地 children/ 缓存查
                children = get_local_children(gal_path, parent_id)
                if children:
                    self.send_ok(make_api_response(children, "_templetData"))
                    return True
                # 2. 再从目录树 JSON 中查（如果树有嵌套 children）
                tree = get_local_tree(gal_path)
                if tree:
                    children = self.find_children_by_id(tree, parent_id)
                    if children:
                        self.send_ok(make_api_response(children, "_templetData"))
                        return True
                # 无缓存 → 回退代理

            elif call_type == "GetNextContent":
                # 翻页功能：本地树为扁平数据，无法翻页
                # 回退代理
                return False

            elif call_type == "QueryContent":
                gal_path = paras.get("galPath", "")
                keyword = paras.get("find", "")
                tree = get_local_tree(gal_path)
                if tree and keyword:
                    results = self.search_tree(tree, keyword)
                    if results:
                        self.send_ok(make_api_response(results, "_templetData"))
                        return True
                    # 本地树扁平可能搜不到 → 回退代理
                    return False

        except Exception as e:
            pass
        return False

    def find_children_by_id(self, nodes, parent_id):
        """在树中查找指定 ID 的子节点"""
        parent_id = str(parent_id)
        for node in nodes:
            node_id = node.get("id", "")
            if node_id == parent_id or node_id == "F" + parent_id:
                if node.get("children"):
                    return node["children"]
                return []
            if node_id.startswith("F"):
                if node_id[1:] == parent_id:
                    if node.get("children"):
                        return node["children"]
                    return []
            if node.get("children"):
                result = self.find_children_by_id(node["children"], parent_id)
                if result:
                    return result
        return []

    def search_tree(self, nodes, keyword):
        """在树中搜索关键词"""
        results = []
        lower_kw = keyword.lower()
        for node in nodes:
            text = node.get("text", "").lower()
            if lower_kw in text:
                results.append({
                    "text": node.get("text", ""),
                    "id": node.get("id", ""),
                    "itempath": node.get("itempath", ""),
                    "itemcontent": node.get("itemcontent", ""),
                    "fullpath": node.get("fullpath", ""),
                })
            if node.get("children"):
                results.extend(self.search_tree(node["children"], keyword))
        return results

    # ===== GET 请求 =====
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = parsed.query

        # 1. 本地前端文件
        if path in LOCAL_FILES or path == "":
            file_to_serve = "index.html" if path in ("/", "") else path.lstrip("/")
            self.serve_local_file("/" + file_to_serve)
            return

        # 2. 本地 API: /api/galList
        if path == "/api/galList":
            data = load_local_json(os.path.join(DATA_DIR, "galList.json"))
            if data:
                self.send_ok(json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8")
            else:
                self.send_error(503, "数据未下载，请先运行 crawler.py")
            return

        # 3. 本地 API: /api/tree/<bookPath>
        match = re.match(r"^/api/tree/(.+)$", path)
        if match:
            book_path = urllib.parse.unquote(match.group(1))
            data = get_local_tree(book_path)
            if data:
                self.send_ok(json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8")
            else:
                self.send_error(404, "Tree not found for: " + book_path)
            return

        # 4. 本地缓存的内容页: /api/content/<bookPath>/<base64path>/<content>
        match = re.match(r"^/api/content/(.+)/(.+)/(.+)$", path)
        if match:
            book_path = urllib.parse.unquote(match.group(1))
            itempath = urllib.parse.unquote(match.group(2))
            itemcontent = urllib.parse.unquote(match.group(3))
            content = get_local_content(book_path, itempath, itemcontent)
            if content:
                self.send_ok(content, "text/html; charset=utf-8")
            else:
                self.send_error(404, "Content not cached")
            return

        # 5. /proxy/ 前缀 → 先尝试本地缓存，再代理
        if path.startswith("/proxy/"):
            upstream_path = path[len("/proxy"):]
            if query:
                upstream_path += "?" + query

            # 拦截图片/CSS/JS，优先本地缓存
            if "/WebSource/" in path or path.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".ico")):
                img_key = upstream_path  # /WebSource/Book/.../xxx.png
                cached = get_local_image(img_key)
                if cached:
                    self.serve_local_file(cached)
                    return
                # 代理并缓存
                self.proxy_upstream_and_cache_image(upstream_path, img_key)
                return

            # 拦截内容页请求，优先本地缓存
            if "/ManualContentPage.aspx" in path:
                qs = urllib.parse.parse_qs(query)
                gal = qs.get("gal", [""])[0]
                ipath = qs.get("path", [""])[0]
                icontent = qs.get("content", [""])[0]
                if gal and ipath and icontent:
                    content = get_local_content(gal, ipath, icontent)
                    if content:
                        self.send_ok(content, "text/html; charset=utf-8")
                        return
                    # 未缓存 → 代理并保存到本地
                    self.proxy_upstream(upstream_path, method="GET",
                                        save_content_key=(gal, ipath, icontent))
                    return

            self.proxy_upstream(upstream_path, method="GET")
            return

        # 6. 其他 GET → 代理（图片、CSS等）
        self.proxy_upstream(self.path, method="GET")

    # ===== POST 请求 =====
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = parsed.query

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        content_type = self.headers.get("Content-Type", "application/x-www-form-urlencoded")

        # 1. 本地 API 端点: /api/
        if path.startswith("/api/"):
            call_type, paras = self.parse_api_body(body)
            if self.handle_local_api(call_type, paras):
                return
            # 本地无法处理 → 返回错误，因为没有代理回退
            try:
                self.send_error(503, "Data not available locally")
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass
            return

        # 2. /proxy/ 前缀 → 先尝试本地缓存，再代理
        if path.startswith("/proxy/"):
            upstream_path = path[len("/proxy"):]
            if query:
                upstream_path += "?" + query

            # 拦截 API 请求：GetChildDrawingDir 写透缓存
            if "/ykyApp.ashx" in path:
                call_type, paras = self.parse_api_body(body)
                if call_type == "GetChildDrawingDir":
                    gal_path = paras.get("galPath", "")
                    parent_id = str(paras.get("parentId", ""))
                    # 先查本地缓存
                    children = get_local_children(gal_path, parent_id)
                    if children:
                        self.send_ok(make_api_response(children, "_templetData"))
                        return
                    # 代理并缓存
                    self.proxy_upstream_and_cache_children(
                        upstream_path, gal_path, parent_id, body, content_type)
                    return

            self.proxy_upstream(upstream_path, method="POST", body=body,
                                extra_headers={"Content-Type": content_type})
            return

        # 3. 尝试本地 API
        call_type, paras = self.parse_api_body(body)
        if self.handle_local_api(call_type, paras):
            return

        # 4. 回退代理
        self.proxy_upstream(self.path, method="POST", body=body,
                            extra_headers={"Content-Type": content_type})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()


def prefetch_deep_children(gal_path, nodes):
    """后台线程：递归预取子节点的子节点（深度优先，最多3层）"""
    def worker():
        _prefetch_recursive(gal_path, nodes, depth=0, max_depth=3)
    t = threading.Thread(target=worker, daemon=True)
    t.start()


def _api_opener():
    """创建独立的 API opener"""
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar())
    )


def _prefetch_recursive(gal_path, nodes, depth, max_depth):
    """递归预取子节点数据"""
    if depth >= max_depth:
        return

    api_url = UPSTREAM + "/ykyapp/App/ykyApp.ashx"
    opener = _api_opener()

    for node in nodes:
        node_id = node.get("id", "")
        pid = node_id[1:] if node_id.startswith("F") else node_id
        cache_key = (gal_path, pid)

        # 跳过已缓存的
        if get_local_children(gal_path, pid):
            continue

        # 控制频率
        time.sleep(0.3)

        try:
            paras_json = json.dumps({
                "galPath": gal_path,
                "subPath": "Book",
                "varData": "_templetData",
                "parentId": int(pid) if pid.isdigit() else pid,
                "userId": "0",
            }, ensure_ascii=False)
            body = urllib.parse.urlencode({
                "callType": "GetChildDrawingDir",
                "callParas": paras_json,
            }).encode("utf-8")

            req = urllib.request.Request(api_url, data=body, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
            req.add_header("User-Agent", "Mozilla/5.0")
            req.add_header("Referer", UPSTREAM + "/ykyapp/App/WebBookIndex.aspx")

            resp = opener.open(req, timeout=20)
            text = resp.read().decode("utf-8")
            match = re.search(r"var retJson=(\{.*?\});", text, re.DOTALL)
            if match:
                ret = json.loads(match.group(1))
                sc = ret.get("scriptCode", "")
                if "_templetData" in sc:
                    children = execute_js_data(sc, "_templetData")
                    if children:
                        save_local_children(gal_path, pid, children)
                        # 递归下一层
                        _prefetch_recursive(gal_path, children, depth + 1, max_depth)
        except Exception:
            pass


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), RequestHandler)
    has_data = os.path.exists(os.path.join(DATA_DIR, "galList.json"))
    print("=" * 60)
    print("  机械工程师设计手册 - 工程手册服务器")
    print("=" * 60)
    print(f"  本地地址:  http://localhost:{PORT}/")
    print(f"  数据缓存:  {'已就绪' if has_data else '未下载（需运行 crawler.py）'}")
    print(f"  上游代理:  {UPSTREAM} (仅在缓存未命中时使用)")
    print("=" * 60)
    print("  按 Ctrl+C 停止服务器")
    print("=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
