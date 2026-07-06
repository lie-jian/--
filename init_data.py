# -*- coding: utf-8 -*-
"""
初始化本地数据 - 一次性保存书目列表和顶层目录树
内容页在用户浏览时自动缓存（写透缓存）
"""
import os, re, json, subprocess, tempfile, time
import urllib.request, urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
UPSTREAM = "http://dev.inkcad.com"
API_URL = UPSTREAM + "/ykyapp/App/ykyApp.ashx"

os.makedirs(os.path.join(DATA_DIR, "trees"), exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, "contents"), exist_ok=True)


def call_api(call_type, paras):
    paras_json = json.dumps(paras, ensure_ascii=False)
    body = urllib.parse.urlencode({"callType": call_type, "callParas": paras_json}).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
    req.add_header("User-Agent", "Mozilla/5.0")
    req.add_header("Referer", UPSTREAM + "/ykyapp/App/WebBookIndex.aspx")
    resp = urllib.request.urlopen(req, timeout=30)
    text = resp.read().decode("utf-8")
    match = re.search(r"var retJson=(\{.*?\});", text, re.DOTALL)
    if not match:
        raise Exception("parse error")
    return json.loads(match.group(1))


def eval_js(sc):
    """用 Node.js 解析 JS"""
    wrap = sc + "\n"
    if "_templetData" in sc:
        wrap += "console.log(JSON.stringify(_templetData));\n"
    elif "_templetList" in sc:
        wrap += "console.log(JSON.stringify(_templetList));\n"
    else:
        return None
    tf = tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False, encoding='utf-8')
    try:
        tf.write(wrap)
        tf.close()
        r = subprocess.run(["node", tf.name], capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout.strip())
    finally:
        try: os.unlink(tf.name)
        except: pass
    return None


def sanitize(name):
    return name.replace("/", "_").replace("\\", "_").replace("?", "_").replace(":", "_") \
               .replace("*", "_").replace('"', "_").replace("<", "_").replace(">", "_").replace("|", "_")


print("=" * 50)
print("  初始化本地数据")
print("=" * 50)

# 1. 书目列表
print("\n[1] 获取书目列表...")
ret = call_api("GetGalList", {"varList": "_templetList", "subPath": "Book", "userId": "0"})
books = eval_js(ret["scriptCode"])
print(f"  {len(books)} 本书")
with open(os.path.join(DATA_DIR, "galList.json"), "w", encoding="utf-8") as f:
    json.dump(books, f, ensure_ascii=False, indent=2)

# 2. 各书顶层目录树
print("\n[2] 获取各书目录树...")
for book in books:
    path = book["path"]
    print(f"  获取: {book['text']}...", end=" ")
    try:
        ret = call_api("GetDrawingDir", {"galPath": path, "subPath": "Book",
                       "varData": "_templetData", "showClass": "0", "userId": "0"})
        nodes = eval_js(ret["scriptCode"])
        if nodes:
            safe = sanitize(path)
            with open(os.path.join(DATA_DIR, "trees", f"{safe}.json"), "w", encoding="utf-8") as f:
                json.dump(nodes, f, ensure_ascii=False, indent=2)
            # 统计总数
            def count(ns):
                c = 0
                for n in ns:
                    c += 1
                    if n.get("children"): c += count(n["children"])
                return c
            print(f"{count(nodes)} 个节点")
        else:
            print("无数据")
    except Exception as e:
        print(f"失败: {e}")
    time.sleep(0.3)

print(f"\n{'=' * 50}")
print(f"  初始化完成! 数据保存在 {DATA_DIR}")
print(f"  启动 server.py 后浏览内容页会自动缓存")
print(f"{'=' * 50}")
