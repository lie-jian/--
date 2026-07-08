# -*- coding: utf-8 -*-
"""
工程手册本地服务器（纯离线版本）
- 仅从本地 data/ 目录读取已下载的缓存数据
- 不访问原站，无代理逻辑
- 启动后即可独立运行
"""

import os
import re
import sys
import json
import urllib.parse
import http.server
import socketserver

# ===== 配置 =====
PORT = 8000
LOCAL_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(LOCAL_DIR, "data")

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
    ".xml": "application/xml; charset=utf-8",
    ".mso": "application/octet-stream",
    ".thmx": "application/octet-stream",
}

# ===== 文件名工具 =====

def sanitize_filename(name):
    """替换 Windows 文件名非法字符"""
    return (name.replace("/", "_").replace("\\", "_").replace("?", "_")
                .replace(":", "_").replace("*", "_").replace('"', "_")
                .replace("<", "_").replace(">", "_").replace("|", "_"))


def split_and_sanitize_path(path_str):
    """按 / 拆分路径并逐段 sanitize，保留目录结构"""
    clean = path_str.lstrip("/").replace("\\", "/")
    parts = clean.split("/")
    result = []
    for p in parts:
        if p == ".." or not p:
            continue
        result.append(sanitize_filename(p))
    return result


# ===== 本地数据访问 =====

def load_local_json(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def get_local_tree(book_path):
    """读取目录树"""
    safe = sanitize_filename(book_path)
    return load_local_json(os.path.join(DATA_DIR, "trees", f"{safe}.json"))


def get_local_children(book_path, parent_id):
    """读取子节点缓存"""
    safe = sanitize_filename(book_path)
    return load_local_json(os.path.join(DATA_DIR, "children", safe, f"{parent_id}.json"))


def get_local_content(book_path, itempath, itemcontent):
    """读取内容页 HTML"""
    safe_book = sanitize_filename(book_path)
    fname = sanitize_filename(itempath + "_" + itemcontent) + ".html"
    fpath = os.path.join(DATA_DIR, "contents", safe_book, fname)
    if os.path.exists(fpath) and os.path.getsize(fpath) > 100:
        with open(fpath, "rb") as f:
            return f.read()
    return None


def get_local_image_path(img_path):
    """返回本地图片文件的绝对路径，不存在则返回 None"""
    parts = split_and_sanitize_path(img_path)
    if not parts:
        return None
    fpath = os.path.join(DATA_DIR, "images", *parts)
    if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
        return fpath
    return None


# ===== API 响应构造（兼容原站格式） =====

def make_api_response(data, var_name="_templetData"):
    """生成原站 API 响应格式：var retJson={result,scriptCode:'var _templetData=[...]'};"""
    script_code = f"var {var_name}=" + json.dumps(data, ensure_ascii=False) + ";"
    ret = {"result": "0", "scriptCode": script_code, "err": ""}
    return ("var retJson=" + json.dumps(ret, ensure_ascii=False) + ";").encode("utf-8")


def make_error_response(err_msg, result="1"):
    ret = {"result": result, "scriptCode": "", "err": err_msg}
    return ("var retJson=" + json.dumps(ret, ensure_ascii=False) + ";").encode("utf-8")


# ===== 树搜索 =====

def search_tree(nodes, keyword):
    """递归搜索目录树"""
    results = []
    lower_kw = keyword.lower()

    def walk(node_list):
        for node in node_list:
            text = node.get("text", "")
            if lower_kw in text.lower():
                results.append({
                    "text": text,
                    "id": node.get("id", ""),
                    "itempath": node.get("itempath", ""),
                    "itemcontent": node.get("itemcontent", ""),
                    "fullpath": node.get("fullpath", ""),
                })
            children = node.get("children")
            if children:
                walk(children)

    walk(nodes)
    return results


def find_children_in_tree(nodes, parent_id):
    """在目录树中递归查找指定 ID 的子节点列表"""
    parent_id = str(parent_id)
    for node in nodes:
        node_id = node.get("id", "")
        # ID 形如 "F123" 或 "123"
        if node_id == parent_id or node_id == "F" + parent_id:
            return node.get("children", [])
        if node_id.startswith("F") and node_id[1:] == parent_id:
            return node.get("children", [])
        children = node.get("children")
        if children:
            result = find_children_in_tree(children, parent_id)
            if result:
                return result
    return None


# ===== HTTP 处理器 =====

class RequestHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # ----- 响应工具 -----
    def send_bytes(self, content, content_type="text/html; charset=utf-8", status=200):
        if isinstance(content, str):
            content = content.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def send_text(self, text, status=200, content_type="text/plain; charset=utf-8"):
        self.send_bytes(text.encode("utf-8"), content_type, status)

    def send_json(self, obj, status=200):
        self.send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8", status)

    def send_api(self, data, var_name="_templetData"):
        self.send_bytes(make_api_response(data, var_name),
                        "application/javascript; charset=utf-8")

    def send_api_error(self, err, result="1"):
        self.send_bytes(make_error_response(err, result),
                        "application/javascript; charset=utf-8")

    def send_not_found(self, msg="Not found"):
        self.send_text(msg, status=404)

    def serve_file(self, abs_path):
        if not os.path.exists(abs_path) or os.path.isdir(abs_path):
            self.send_not_found()
            return
        ext = os.path.splitext(abs_path)[1].lower()
        with open(abs_path, "rb") as f:
            content = f.read()
        self.send_bytes(content, MIME_TYPES.get(ext, "application/octet-stream"))

    # ----- 静态前端文件 -----
    def serve_frontend(self, path):
        if path == "/" or path == "":
            target = "index.html"
        else:
            target = path.lstrip("/")
        full = os.path.join(LOCAL_DIR, target)
        if not os.path.exists(full) or os.path.isdir(full):
            self.send_not_found()
            return
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            content = f.read()
        self.send_bytes(content, MIME_TYPES.get(ext, "application/octet-stream"))

    # ----- API 处理 -----
    def parse_api_body(self, body):
        try:
            params = urllib.parse.parse_qs(body.decode("utf-8") if isinstance(body, bytes) else body)
        except Exception:
            return "", {}
        call_type = params.get("callType", [""])[0]
        call_paras_str = params.get("callParas", ["{}"])[0]
        try:
            paras = json.loads(call_paras_str)
        except Exception:
            paras = {}
        return call_type, paras

    def handle_api(self, call_type, paras):
        """处理原站 API 调用，返回本地数据"""
        if call_type == "GetGalList":
            data = load_local_json(os.path.join(DATA_DIR, "galList.json"))
            if data:
                self.send_api(data, "_templetList")
            else:
                self.send_api_error("galList.json 未下载")
            return

        if call_type == "GetDrawingDir":
            gal_path = paras.get("galPath", "")
            data = get_local_tree(gal_path)
            if data:
                self.send_api(data, "_templetData")
            else:
                self.send_api_error("目录树未下载: " + gal_path)
            return

        if call_type == "GetChildDrawingDir":
            gal_path = paras.get("galPath", "")
            parent_id = str(paras.get("parentId", ""))
            # 1. 直接从 children/ 缓存查
            children = get_local_children(gal_path, parent_id)
            if children is not None:
                self.send_api(children, "_templetData")
                return
            # 2. 从目录树中递归查找
            tree = get_local_tree(gal_path)
            if tree:
                found = find_children_in_tree(tree, parent_id)
                if found is not None:
                    self.send_api(found, "_templetData")
                    return
            # 没有数据 → 返回空数组（节点可能是叶子）
            self.send_api([], "_templetData")
            return

        if call_type == "QueryContent":
            gal_path = paras.get("galPath", "")
            keyword = paras.get("find", "")
            if not keyword:
                self.send_api([], "_templetData")
                return
            tree = get_local_tree(gal_path)
            if not tree:
                self.send_api_error("目录树未下载: " + gal_path)
                return
            # 本地树是扁平结构，需要加载所有 children 缓存拼成完整树再搜
            full_tree = self._build_full_tree(gal_path, tree)
            results = search_tree(full_tree, keyword)
            self.send_api(results, "_templetData")
            return

        if call_type == "GetNextContent":
            # 翻页功能：本地实现 - 在扁平节点列表中找当前节点的下一个
            gal_path = paras.get("galPath", "")
            current_id = str(paras.get("id", ""))
            direction = str(paras.get("next", "1"))
            tree = get_local_tree(gal_path)
            if not tree:
                self.send_api_error("目录树未下载")
                return
            full_tree = self._build_full_tree(gal_path, tree)
            flat = self._flatten_tree(full_tree)
            # 找当前节点位置
            idx = -1
            for i, node in enumerate(flat):
                nid = node.get("id", "")
                nid_num = nid[1:] if nid.startswith("F") else nid
                if nid == current_id or nid_num == current_id:
                    idx = i
                    break
            if idx == -1:
                self.send_api([], "_templetData")
                return
            step = 1 if direction == "1" else -1
            new_idx = idx + step
            if 0 <= new_idx < len(flat):
                # 只返回有 itempath 的叶子节点
                target = flat[new_idx]
                if target.get("itempath") and target.get("itemcontent"):
                    self.send_api([target], "_templetData")
                    return
            self.send_api([], "_templetData")
            return

        # 未知 callType
        self.send_api_error("未知调用: " + call_type)

    def _build_full_tree(self, gal_path, nodes):
        """将扁平的 children 缓存合并到目录树，构造完整树"""
        safe = sanitize_filename(gal_path)
        cdir = os.path.join(DATA_DIR, "children", safe)

        def attach_children(node_list):
            for node in node_list:
                node_id = node.get("id", "")
                pid = node_id[1:] if node_id.startswith("F") else node_id
                # 如果节点已有 children，递归
                if node.get("children"):
                    attach_children(node["children"])
                else:
                    # 从缓存加载
                    cpath = os.path.join(cdir, f"{pid}.json")
                    cached = load_local_json(cpath)
                    if cached:
                        node["children"] = cached
                        attach_children(cached)

        # 深拷贝避免污染
        import copy
        result = copy.deepcopy(nodes)
        attach_children(result)
        return result

    def _flatten_tree(self, nodes):
        """扁平化树为节点列表（仅叶子节点：有 itempath）"""
        result = []
        def walk(node_list):
            for node in node_list:
                if node.get("itempath") and node.get("itemcontent"):
                    result.append(node)
                children = node.get("children")
                if children:
                    walk(children)
        walk(nodes)
        return result

    # ----- GET 路由 -----
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = parsed.query

        # 1. 前端静态文件
        if path in LOCAL_FILES or path == "":
            self.serve_frontend(path)
            return

        # 2. /api/galList （备用 GET 入口）
        if path == "/api/galList":
            data = load_local_json(os.path.join(DATA_DIR, "galList.json"))
            if data:
                self.send_json(data)
            else:
                self.send_not_found("galList not found")
            return

        # 3. /api/tree/<bookPath>
        m = re.match(r"^/api/tree/(.+)$", path)
        if m:
            book_path = urllib.parse.unquote(m.group(1))
            data = get_local_tree(book_path)
            if data:
                self.send_json(data)
            else:
                self.send_not_found("Tree not found: " + book_path)
            return

        # 4. /api/content/<bookPath>/<itempath>/<itemcontent>
        m = re.match(r"^/api/content/(.+)/(.+)/(.+)$", path)
        if m:
            book_path = urllib.parse.unquote(m.group(1))
            itempath = urllib.parse.unquote(m.group(2))
            itemcontent = urllib.parse.unquote(m.group(3))
            content = get_local_content(book_path, itempath, itemcontent)
            if content:
                self.send_bytes(content, "text/html; charset=utf-8")
            else:
                self.send_not_found("Content not cached")
            return

        # 5. 兼容原站路径: /ykyapp/App/ManualContentPage.aspx?gal=&path=&content=
        if "/ManualContentPage.aspx" in path:
            qs = urllib.parse.parse_qs(query)
            gal = qs.get("gal", [""])[0]
            ipath = qs.get("path", [""])[0]
            icontent = qs.get("content", [""])[0]
            if gal and ipath and icontent:
                content = get_local_content(gal, ipath, icontent)
                if content:
                    self.send_bytes(content, "text/html; charset=utf-8")
                    return
            self.send_not_found("内容页未缓存")
            return

        # 6. 图片/CSS/字体等静态资源（路径以 /WebSource/ 等开头）
        # 处理 /WebSource/..., /ykyapp/App/WebSource/..., /images/...
        img_candidates = []
        if path.startswith("/WebSource/"):
            img_candidates.append(path)
        elif "/WebSource/" in path:
            # 兼容 /ykyapp/App/../../WebSource/... 形式
            idx = path.find("/WebSource/")
            img_candidates.append(path[idx:])
        elif path.startswith("/images/"):
            img_candidates.append(path)

        # 处理 .files/ 目录下的资源（如 1.files/image001.jpg，相对路径会请求 /ykyapp/App/1.files/...）
        if not img_candidates:
            ext = os.path.splitext(path)[1].lower()
            if ext in (".jpg", ".jpeg", ".png", ".gif", ".svg", ".css", ".xml",
                       ".mso", ".thmx", ".woff", ".woff2", ".ttf", ".eot"):
                # 尝试从 query 中获取相对路径，或直接尝试在 images 目录中查找
                pass

        for cand in img_candidates:
            # 同时尝试原始(URL编码)和解码后(中文)两种路径，因为本地存储用中文目录名
            for variant in (cand, urllib.parse.unquote(cand)):
                local_path = get_local_image_path(variant)
                if local_path:
                    self.serve_file(local_path)
                    return

        # 7. 兜底：返回 404
        self.send_not_found("Not found: " + path)

    # ----- POST 路由 -----
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        # 1. /api/ 端点
        if path.startswith("/api/"):
            call_type, paras = self.parse_api_body(body)
            self.handle_api(call_type, paras)
            return

        # 2. 兼容原站 API 路径: /ykyapp/App/ykyApp.ashx
        if "/ykyApp.ashx" in path:
            call_type, paras = self.parse_api_body(body)
            self.handle_api(call_type, paras)
            return

        # 3. 其他 POST → 404
        self.send_not_found("POST not handled: " + path)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main():
    has_data = os.path.exists(os.path.join(DATA_DIR, "galList.json"))
    print("=" * 60)
    print("  机械工程师设计手册 - 本地服务器（纯离线）")
    print("=" * 60)
    print(f"  本地地址:  http://localhost:{PORT}/")
    print(f"  数据目录:  {DATA_DIR}")
    print(f"  数据状态:  {'已就绪' if has_data else '未下载（请先运行 download_all.py）'}")
    print("=" * 60)
    print("  按 Ctrl+C 停止服务器")
    print("=" * 60)
    try:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), RequestHandler)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
