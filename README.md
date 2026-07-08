# 机械工程师设计手册 - 本地克隆版

对 `dev.inkcad.com` 工程手册网站的完整离线克隆，纯本地运行，无需联网。

## 功能

-   **完整离线**：所有数据（目录树、内容页、图片）本地存储，断网可用
-   **目录树浏览**：BFS 分层懒加载，支持展开/折叠、搜索节点
-   **内容查看**：iframe 内嵌内容页，支持上/下翻页
-   **蒸汽朋克 UI**：深色主题配黄铜/铜锈配色，同时保证正文可读性

## 技术栈

| 层 | 技术 |
|---|---|
| 服务器 | Python `http.server` + `ThreadingMixIn` |
| 前端 | 原生 JS（零依赖） |
| 界面 | HTML + CSS（CSS 变量驱动） |
| 数据格式 | JSON（目录/子节点）、HTML（内容页）、原始图片 |

## 项目结构

```
├── server.py          # 本地 HTTP 服务器（纯离线）
├── app.js             # 前端逻辑（目录树、搜索、翻页）
├── index.html         # 页面结构
├── style.css          # 蒸汽朋克主题样式
├── download_all.py    # 离线数据下载器
├── fetch_images.py    # 图片补下载器（支持续传）
└── data/              # 本地数据目录（gitignore）
    ├── galList.json   # 书目列表
    ├── trees/         # 各书目录树
    ├── children/      # 子节点缓存
    ├── contents/      # 内容页 HTML
    └── images/        # 图片文件
```

## 快速启动

```bash
# 安装 Python 3（无需额外依赖，标准库即可）

# 启动服务器
python server.py

# 浏览器访问
http://localhost:8000/
```

## 下载离线数据

如果 `data/` 目录为空，需先从原站下载数据。

### 1. 下载结构化数据和内容页

```bash
python download_all.py
```

下载内容：书目列表 → 目录树 → 子节点 → 内容页 HTML（自动瘦身、编码转换）。

### 2. 下载图片

```bash
python fetch_images.py
```

支持断点续传：已下载的图片自动跳过，中断后重跑即可继续。

### 配置

两个下载器的顶部均有配置项：

```python
UPSTREAM = "http://dev.inkcad.com"  # 原站地址
MAX_WORKERS = 2                      # 并发线程数（过大可能被封）
RETRY_TIMES = 3                      # 失败重试次数
```

## API 端点

服务器兼容原站 API 格式，前端无需修改：

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/` | POST | 通用 API（GetGalList / GetDrawingDir / GetChildDrawingDir / QueryContent / GetNextContent） |
| `/api/galList` | GET | 备用：获取书目列表 |
| `/api/tree/<book>` | GET | 备用：获取目录树 |
| `/api/content/<book>/<path>/<content>` | GET | 备用：获取内容页 |
| `/ykyapp/App/ManualContentPage.aspx` | GET | 兼容原站内容页路径 |

## 数据规模

| 数据类型 | 数量 | 大小 |
|---|---|---|
| 内容页 | 16,586 | 635 MB |
| 子节点缓存 | 20,793 | ~80 MB |
| 图片 | 43,468 | 698 MB |
| **合计** | | **~1.4 GB** |

## 技术要点

-   **编码转换**：原站 gb2312 内容页 → utf-8 本地存储
-   **HTML 瘦身**：裁剪 ASP.NET 包装标签，只保留核心内容
-   **图片路径**：相对路径 `../../WebSource/...` → 标准化路径 → 本地 `data/images/` 目录
-   **URL 编码**：中文路径分段 `urllib.parse.quote()` 编码，保留 `/` 分隔符
-   **翻页**：扁平化完整目录树 → 查找相邻叶子节点 → 返回上一页/下一页
-   **断点续传**：基于文件系统检查，已有文件自动跳过

## 许可

仅供个人学习使用。原始数据版权归原网站所有。
