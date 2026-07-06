# -*- coding: utf-8 -*-
"""
离线下载器 - 基于本地缓存树，自动下载全部子节点和内容页
将 localhost:8000 作为代理触发写透缓存，逐个请求即可。
可随时中断，下次运行会跳过已缓存的。
"""
import os, sys, json, time, urllib.request, urllib.parse

BASE = "http://localhost:8000"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def sanitize(name):
    return name.replace("/", "_").replace("\\", "_").replace("?", "_").replace(":", "_") \
               .replace("*", "_").replace('"', "_").replace("<", "_").replace(">", "_").replace("|", "_")


def load_json(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def is_children_cached(book_path, parent_id):
    cpath = os.path.join(DATA_DIR, "children", sanitize(book_path), f"{parent_id}.json")
    return os.path.exists(cpath) and os.path.getsize(cpath) > 50


def is_content_cached(book_path, itempath, itemcontent):
    cdir = os.path.join(DATA_DIR, "contents", sanitize(book_path))
    fname = sanitize(itempath + "_" + itemcontent) + ".html"
    return os.path.exists(os.path.join(cdir, fname))


def call_api(call_type, paras):
    paras_json = json.dumps(paras, ensure_ascii=False)
    body = urllib.parse.urlencode({"callType": call_type, "callParas": paras_json}).encode("utf-8")
    req = urllib.request.Request(BASE + "/proxy/ykyapp/App/ykyApp.ashx", data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
    resp = urllib.request.urlopen(req, timeout=30)
    return resp.read()


def fetch_content(book_path, itempath, itemcontent):
    url = f"{BASE}/proxy/ykyapp/App/ManualContentPage.aspx?gal={urllib.parse.quote(book_path)}&path={urllib.parse.quote(itempath)}&content={urllib.parse.quote(itemcontent)}"
    try:
        r = urllib.request.urlopen(url, timeout=30)
        return len(r.read()) > 500
    except:
        return False


def collect_all_nodes(book_path, nodes, result=None):
    if result is None:
        result = []
    for node in nodes:
        if node.get("itempath") and node.get("itemcontent"):
            result.append({
                "id": node.get("id", ""),
                "text": node.get("text", ""),
                "itempath": node["itempath"],
                "itemcontent": node["itemcontent"],
            })
        # 递归读取本地缓存中的子节点
        node_id = node.get("id", "")
        pid = node_id[1:] if node_id.startswith("F") else node_id
        children = load_json(
            os.path.join(DATA_DIR, "children", sanitize(book_path), f"{pid}.json")
        )
        if children:
            collect_all_nodes(book_path, children, result)
        elif node.get("children"):
            collect_all_nodes(book_path, node["children"], result)
    return result


def main():
    print("=" * 60)
    print("  离线下载器 - 遍历全部节点触发缓存")
    print("  依赖 server.py 正在运行 (localhost:8000)")
    print("=" * 60)

    books = load_json(os.path.join(DATA_DIR, "galList.json"))
    if not books:
        print("错误: 请先运行 init_data.py")
        return

    # 收集所有需要下载的内容
    all_tasks = []  # [(book_path, node), ...]
    all_nodes = {}  # book_path -> [nodes]

    for book in books:
        path = book["path"]
        tree = load_json(os.path.join(DATA_DIR, "trees", f"{sanitize(path)}.json"))
        if not tree:
            continue
        nodes = collect_all_nodes(path, tree)
        all_nodes[path] = nodes
        for n in nodes:
            all_tasks.append((path, n))

    # 统计
    total = len(all_tasks)
    children_needed = 0
    content_needed = 0
    for book_path, node in all_tasks:
        node_id = node["id"]
        pid = node_id[1:] if node_id.startswith("F") else node_id
        if not is_children_cached(book_path, pid):
            children_needed += 1
        if not is_content_cached(book_path, node["itempath"], node["itemcontent"]):
            content_needed += 1

    print(f"\n总节点数: {total}")
    print(f"待下载子节点: {children_needed}")
    print(f"待下载内容页: {content_needed}")
    print(f"预计耗时: ~{children_needed * 1 + content_needed * 0.5:.0f} 秒\n")

    if children_needed == 0 and content_needed == 0:
        print("全部已缓存，无需下载。")
        return

    # 下载子节点数据
    done_c = 0
    for book_path, node in all_tasks:
        node_id = node["id"]
        pid = node_id[1:] if node_id.startswith("F") else node_id

        if not is_children_cached(book_path, pid):
            done_c += 1
            try:
                call_api("GetChildDrawingDir", {
                    "galPath": book_path, "subPath": "Book",
                    "varData": "_templetData",
                    "parentId": int(pid) if pid.isdigit() else pid,
                    "userId": "0",
                })
                sys.stdout.write(f"\r子节点: {done_c}/{children_needed}")
                sys.stdout.flush()
            except Exception as e:
                pass
            time.sleep(0.3)

    if children_needed > 0:
        print()

    # 下载内容页
    done_p = 0
    for book_path, node in all_tasks:
        if not is_content_cached(book_path, node["itempath"], node["itemcontent"]):
            done_p += 1
            try:
                fetch_content(book_path, node["itempath"], node["itemcontent"])
                sys.stdout.write(f"\r内容页: {done_p}/{content_needed}")
                sys.stdout.flush()
            except Exception as e:
                pass
            time.sleep(0.15)

    if content_needed > 0:
        print()

    print(f"\n下载完成!")
    print(f"  子节点: {done_c} 个")
    print(f"  内容页: {done_p} 页")


if __name__ == "__main__":
    main()
