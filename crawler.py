# -*- coding: utf-8 -*-
"""
数据爬虫 - 下载所有内容页和API数据到本地
运行后会生成 data/ 目录：
  data/galList.json     - 书目列表
  data/trees/           - 各书目录树
  data/contents/        - 内容页 HTML
  data/manifest.json    - 内容清单
"""
import os
import re
import json
import time
import subprocess
import urllib.request
import urllib.parse
import urllib.error
from http.cookiejar import CookieJar

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
UPSTREAM = "http://dev.inkcad.com"
API_URL = UPSTREAM + "/ykyapp/App/ykyApp.ashx"
CONTENT_URL = UPSTREAM + "/ykyapp/App/ManualContentPage.aspx"

# ===== 准备工作 =====
os.makedirs(os.path.join(DATA_DIR, "trees"), exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, "contents"), exist_ok=True)

cookie_jar = CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))


def call_api(call_type, paras, retry=3):
    """调用原站API"""
    paras_json = json.dumps(paras, ensure_ascii=False)
    body = urllib.parse.urlencode({
        "callType": call_type,
        "callParas": paras_json,
    }).encode("utf-8")

    req = urllib.request.Request(API_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    req.add_header("Referer", UPSTREAM + "/ykyapp/App/WebBookIndex.aspx")

    for attempt in range(retry):
        try:
            resp = opener.open(req, timeout=30)
            text = resp.read().decode("utf-8")
            # 解析响应
            ret_json = parse_api_response(text)
            if ret_json["result"] != "0":
                raise Exception("API错误: " + json.dumps(ret_json, ensure_ascii=False)[:200])
            return ret_json
        except Exception as e:
            if attempt < retry - 1:
                time.sleep(2 * (attempt + 1))
            else:
                raise


def parse_api_response(text):
    """解析 var retJson={...}; 格式的响应"""
    match = re.search(r"var retJson=(\{.*?\});", text, re.DOTALL)
    if not match:
        raise Exception("无法解析retJson: " + text[:200])
    return json.loads(match.group(1))


def execute_script_code_js(script_code):
    """使用 Node.js 执行 JS 代码，输出 JSON 到 stdout"""
    if not script_code:
        return []
    import tempfile

    # 构造 JS 代码: 执行 scriptCode，输出结果
    wrap = script_code + "\n"
    if "_templetData" in script_code:
        wrap += "console.log(JSON.stringify(_templetData));\n"
    elif "_templetList" in script_code:
        wrap += "console.log(JSON.stringify(_templetList));\n"
    else:
        return []

    # 写入临时文件（避免命令行长度限制）
    tf = tempfile.NamedTemporaryFile(
        mode='w', suffix='.js', delete=False, encoding='utf-8')
    try:
        tf.write(wrap)
        tf.close()
        result = subprocess.run(
            ["node", tf.name],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8",
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
        if result.stderr:
            print("  [NODE stderr]", result.stderr[:200])
    except Exception as e:
        print("  [NODE error]", str(e)[:200])
    finally:
        try:
            os.unlink(tf.name)
        except:
            pass
    return []


def collect_page_nodes(nodes, result=None):
    """递归收集所有带有 itempath 和 itemcontent 的节点"""
    if result is None:
        result = []
    for node in nodes:
        if node.get("itempath") and node.get("itemcontent"):
            result.append({
                "text": node.get("text", ""),
                "id": node.get("id", ""),
                "itempath": node["itempath"],
                "itemcontent": node["itemcontent"],
                "fullpath": node.get("fullpath", ""),
            })
        if node.get("children"):
            collect_page_nodes(node["children"], result)
    return result


def count_nodes_recursive(nodes):
    """统计树中总节点数（含所有层级）"""
    c = 0
    for n in nodes:
        c += 1
        if n.get("children"):
            c += count_nodes_recursive(n["children"])
    return c


def fetch_full_tree(gal_path, depth=0, max_depth=10):
    """递归获取完整目录树（含所有子节点）"""
    # 获取当前层级
    if depth == 0:
        ret = call_api("GetDrawingDir", {
            "galPath": gal_path,
            "subPath": "Book",
            "varData": "_templetData",
            "showClass": "0",
            "userId": "0",
        })
        nodes = execute_script_code_js(ret.get("scriptCode", ""))
    else:
        # 子节点默认不超过100层
        return []

    # 对每个节点，尝试获取子节点
    for node in nodes:
        node_id = node.get("id", "")
        # 去掉 F 前缀取数字 ID
        pid = node_id[1:] if node_id.startswith("F") else node_id

        # 跳过已标记有 children 的（如果 API 已返回）
        if node.get("children"):
            node["children"] = fetch_full_tree_children(gal_path, node["children"], depth + 1, max_depth)
            continue

        # 尝试懒加载子节点
        try:
            time.sleep(0.2)
            child_ret = call_api("GetChildDrawingDir", {
                "galPath": gal_path,
                "subPath": "Book",
                "varData": "_templetData",
                "parentId": int(pid) if pid.isdigit() else pid,
                "userId": "0",
            })
            children = execute_script_code_js(child_ret.get("scriptCode", ""))
            if children:
                node["children"] = fetch_full_tree_children(gal_path, children, depth + 1, max_depth)
        except Exception as e:
            pass

    return nodes


def fetch_full_tree_children(gal_path, nodes, depth, max_depth):
    """递归处理已有子节点列表"""
    if depth >= max_depth:
        return nodes
    for node in nodes:
        node_id = node.get("id", "")
        pid = node_id[1:] if node_id.startswith("F") else node_id

        if node.get("children"):
            node["children"] = fetch_full_tree_children(gal_path, node["children"], depth + 1, max_depth)
            continue

        try:
            time.sleep(0.2)
            child_ret = call_api("GetChildDrawingDir", {
                "galPath": gal_path,
                "subPath": "Book",
                "varData": "_templetData",
                "parentId": int(pid) if pid.isdigit() else pid,
                "userId": "0",
            })
            children = execute_script_code_js(child_ret.get("scriptCode", ""))
            if children:
                node["children"] = fetch_full_tree_children(gal_path, children, depth + 1, max_depth)
        except Exception as e:
            pass
    return nodes
def download_content(gal_path, itempath, itemcontent, retry=3):
    """下载单个内容页"""
    url = f"{CONTENT_URL}?gal={urllib.parse.quote(gal_path)}&path={urllib.parse.quote(itempath)}&content={urllib.parse.quote(itemcontent)}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    req.add_header("Referer", UPSTREAM + "/ykyapp/App/WebBookIndex.aspx")

    for attempt in range(retry):
        try:
            resp = opener.open(req, timeout=30)
            content = resp.read()
            return content
        except Exception as e:
            if attempt < retry - 1:
                time.sleep(2 * (attempt + 1))
            else:
                raise


def sanitize_filename(name):
    """安全的文件名"""
    # 替换路径分隔符和特殊字符
    name = name.replace("/", "_").replace("\\", "_")
    name = name.replace("?", "_").replace(":", "_")
    name = name.replace("*", "_").replace('"', "_")
    name = name.replace("<", "_").replace(">", "_").replace("|", "_")
    return name


def get_content_dir(book_path):
    """获取某本书的内容目录"""
    safe = sanitize_filename(book_path)
    d = os.path.join(DATA_DIR, "contents", safe)
    os.makedirs(d, exist_ok=True)
    return d


# ===== 主流程 =====
def main():
    print("=" * 60)
    print("  数据爬虫 - 下载所有内容到本地")
    print("=" * 60)

    # Step 1: 获取书目列表
    print("\n[1/4] 获取书目列表...")
    ret = call_api("GetGalList", {
        "varList": "_templetList",
        "subPath": "Book",
        "userId": "0",
    })
    books = execute_script_code_js(ret.get("scriptCode", ""))
    print(f"  找到 {len(books)} 本书")
    for b in books:
        print(f"    [{b['id']}] {b['text']}  (path={b['path']})")

    # 保存书目列表
    with open(os.path.join(DATA_DIR, "galList.json"), "w", encoding="utf-8") as f:
        json.dump(books, f, ensure_ascii=False, indent=2)

    # Step 2: 获取每本书的目录树
    print("\n[2/4] 获取各书目录树...")
    all_nodes = {}  # book_path -> [nodes]

    for book in books:
        path = book["path"]
        print(f"  获取: {book['text']}...")
        try:
            nodes = fetch_full_tree(path)
            all_nodes[path] = nodes
            total_in_tree = count_nodes_recursive(nodes)
            print(f"    顶层节点: {len(nodes)} 个, 总计: {total_in_tree} 个")

            # 保存目录树
            safe = sanitize_filename(path)
            with open(os.path.join(DATA_DIR, "trees", f"{safe}.json"), "w", encoding="utf-8") as f:
                json.dump(nodes, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"    失败: {e}")
            all_nodes[path] = []

    # Step 3: 收集需要下载的内容页
    print("\n[3/4] 收集内容页列表...")
    all_pages = []  # [(book_path, itempath, itemcontent, text), ...]
    total = 0

    for book_path, nodes in all_nodes.items():
        pages = collect_page_nodes(nodes)
        for p in pages:
            all_pages.append((book_path, p["itempath"], p["itemcontent"], p["text"]))
        print(f"  {book_path}: 可下载 {len(pages)} 页")
        total += len(pages)

    print(f"\n  总计: {total} 页需要下载")

    # 保存清单
    manifest = [
        {"book": bp, "itempath": ip, "itemcontent": ic, "text": t}
        for bp, ip, ic, t in all_pages
    ]
    with open(os.path.join(DATA_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # Step 4: 下载内容页
    print(f"\n[4/4] 下载内容页 (共 {total} 页)...")
    success = 0
    fail = 0

    for i, (book_path, itempath, itemcontent, text) in enumerate(all_pages):
        # 进度
        if (i + 1) % 20 == 0 or i == 0:
            print(f"  进度: {i + 1}/{total} ({100 * (i + 1) // total}%)")

        # 文件名: <itempath>_<itemcontent>.html (安全化)
        fname = sanitize_filename(itempath + "_" + itemcontent) + ".html"
        fpath = os.path.join(get_content_dir(book_path), fname)

        # 跳过已存在的
        if os.path.exists(fpath) and os.path.getsize(fpath) > 500:
            success += 1
            continue

        try:
            content = download_content(book_path, itempath, itemcontent)
            with open(fpath, "wb") as f:
                f.write(content)
            success += 1
            # 控制下载速度
            time.sleep(0.3)
        except Exception as e:
            fail += 1
            print(f"  ! 失败 ({i + 1}): {text[:30]}... 错误: {e}")

    print(f"\n{'=' * 60}")
    print(f"  下载完成!")
    print(f"  成功: {success} 页")
    print(f"  失败: {fail} 页")
    print(f"  数据目录: {DATA_DIR}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
