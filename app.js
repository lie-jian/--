
/* ============================================================
 * 机械工程师设计手册 - 前端逻辑 v3（纯本地）
 * 所有数据来自本地 data/ 目录，无需访问原站
 * ============================================================ */

(function () {
    'use strict';

    // ===== 配置 =====
    const LOCAL_API = '/api/';
    const CONTENT_PAGE = '/ykyapp/App/ManualContentPage.aspx';

    // ===== 全局状态 =====
    const state = {
        bookList: [],
        currentBook: null,
        treeData: [],
        loadedNodes: new Set(),
        selectedNodeId: null,
    };

    // ===== DOM 元素 =====
    const $ = (id) => document.getElementById(id);
    const dom = {
        cmbList: $('cmbList'),
        txtFind: $('txtFind'),
        btnQuery: $('btnQuery'),
        hrefPrvPage: $('hrefPrvPage'),
        hrefNextPage: $('hrefNextPage'),
        treeContainer: $('treeContainer'),
        searchResults: $('searchResults'),
        contentEmpty: $('contentEmpty'),
        iframe: $('idManualContent'),
        sidebar: $('sidebar'),
        sidebarToggle: $('sidebarToggle'),
        resizer: $('resizer'),
        hidActiveGalPath: $('hidActiveGalPath'),
        hidActiveDirId: $('hidActiveDirId'),
        tabs: document.querySelectorAll('.sidebar-tab'),
        panels: document.querySelectorAll('.sidebar-panel'),
    };

    // ============================================================
    // API 调用层（纯本地）
    // ============================================================

    function callApi(callType, paras) {
        const parasJson = JSON.stringify(paras);
        const body = `callType=${encodeURIComponent(callType)}&callParas=${encodeURIComponent(parasJson)}`;

        return fetch(LOCAL_API, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
            body: body,
        })
            .then((resp) => {
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                return resp.text();
            })
            .then((text) => parseApiResponse(text));
    }

    function parseApiResponse(responseText) {
        if (!responseText || responseText.length === 0) {
            throw new Error('Empty response');
        }
        const trimmed = responseText.trim();
        if (!trimmed.startsWith('var retJson')) {
            throw new Error('Invalid response (not retJson)');
        }
        let retJson;
        try {
            const fn = new Function(responseText + '; return retJson;');
            retJson = fn();
        } catch (e) {
            throw new Error('Parse error: ' + e.message);
        }
        if (!retJson) throw new Error('No retJson in response');
        if (retJson.result !== '0') throw new Error('API error: ' + (retJson.err || retJson.result));

        let data = null;
        if (retJson.scriptCode && retJson.scriptCode.length > 0) {
            try {
                const fn = new Function(
                    retJson.scriptCode +
                    '; return typeof _templetData !== "undefined" ? _templetData : (typeof _templetList !== "undefined" ? _templetList : null);'
                );
                data = fn();
            } catch (e) { data = null; }
        }
        return { retJson, data };
    }

    // ============================================================
    // 书目列表
    // ============================================================

    function loadBookList() {
        const paras = {
            varList: '_templetList',
            subPath: 'Book',
            userId: '0',
        };

        return callApi('GetGalList', paras)
            .then(({ data }) => {
                if (Array.isArray(data)) {
                    state.bookList = data;
                    renderBookList();
                }
            })
            .catch((err) => {
                console.error('加载书目列表失败:', err);
                dom.cmbList.innerHTML = '<option value="">加载失败</option>';
            });
    }

    function renderBookList() {
        dom.cmbList.innerHTML = '';
        state.bookList.forEach((book) => {
            const opt = document.createElement('option');
            opt.value = book.id;
            opt.textContent = book.text;
            opt.dataset.path = book.path;
            opt.dataset.code = book.code || '';
            dom.cmbList.appendChild(opt);
        });
        const defaultBook = state.bookList.find((b) => b.path === '机械工程师设计手册') || state.bookList[0];
        if (defaultBook) {
            dom.cmbList.value = defaultBook.id;
            selectBook(defaultBook);
        }
    }

    function selectBook(book) {
        state.currentBook = book;
        $('hidActiveGalPath').value = book.path;
        state.loadedNodes.clear();
        state.selectedNodeId = null;
        showContentEmpty(true);
        loadTree(book.path);
    }

    // ============================================================
    // 目录树
    // ============================================================

    function loadTree(galPath) {
        dom.treeContainer.innerHTML = `
            <div class="loading">
                <div class="spinner"></div>
                <span>加载目录中...</span>
            </div>`;

        const paras = {
            galPath: galPath,
            subPath: 'Book',
            varData: '_templetData',
            showClass: '0',
            userId: '0',
        };

        return callApi('GetDrawingDir', paras)
            .then(({ data }) => {
                if (Array.isArray(data)) {
                    state.treeData = data;
                    renderTree(data);
                } else {
                    dom.treeContainer.innerHTML = '<div class="empty-state"><p>无目录数据</p></div>';
                }
            })
            .catch((err) => {
                console.error('加载目录树失败:', err);
                dom.treeContainer.innerHTML =
                    '<div class="empty-state"><p>加载目录失败</p><p style="font-size:12px">' + err.message + '</p></div>';
            });
    }

    function renderTree(nodes) {
        dom.treeContainer.innerHTML = '';
        if (!nodes || nodes.length === 0) {
            dom.treeContainer.innerHTML = '<div class="empty-state"><p>无目录数据</p></div>';
            return;
        }
        const root = document.createElement('div');
        root.className = 'tree-root';
        nodes.forEach((node) => root.appendChild(createTreeNode(node, 0)));
        dom.treeContainer.appendChild(root);
    }

    function createTreeNode(node, depth) {
        const hasChildren = node.children && node.children.length > 0;
        const wrapper = document.createElement('div');
        wrapper.className = 'tree-node';
        wrapper.dataset.nodeId = node.id;

        const item = document.createElement('div');
        item.className = 'tree-item';
        item.style.paddingLeft = (8 + depth * 16) + 'px';

        const toggle = document.createElement('span');
        toggle.className = 'tree-toggle' + (hasChildren ? '' : ' no-children');
        toggle.innerHTML = hasChildren
            ? '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="9 18 15 12 9 6"></polyline></svg>'
            : '';
        item.appendChild(toggle);

        const icon = document.createElement('span');
        icon.className = 'tree-icon';
        icon.textContent = hasChildren ? '📁' : '📄';
        item.appendChild(icon);

        const text = document.createElement('span');
        text.className = 'tree-text';
        text.textContent = node.text;
        item.appendChild(text);

        wrapper.appendChild(item);

        let childrenContainer = null;
        if (hasChildren) {
            childrenContainer = document.createElement('div');
            childrenContainer.className = 'tree-children';
            node.children.forEach((child) => {
                childrenContainer.appendChild(createTreeNode(child, depth + 1));
            });
            wrapper.appendChild(childrenContainer);
            if (depth === 0) {
                childrenContainer.classList.add('expanded');
                toggle.classList.add('expanded');
            }
        }

        item.addEventListener('click', (e) => {
            e.stopPropagation();
            handleNodeClick(node, item, toggle, childrenContainer, depth);
        });

        return wrapper;
    }

    function handleNodeClick(node, itemEl, toggleEl, childrenContainer, depth) {
        document.querySelectorAll('.tree-item.selected').forEach((el) => el.classList.remove('selected'));
        itemEl.classList.add('selected');
        state.selectedNodeId = node.id;

        const numericId = node.id.startsWith('F') ? node.id.substring(1) : node.id;
        $('hidActiveDirId').value = numericId;

        loadContent(node.itempath, node.itemcontent);

        const hasChildren = node.children && node.children.length > 0;
        if (hasChildren && !childrenContainer) {
            childrenContainer = itemEl.parentNode.querySelector('.tree-children');
        }
        if (hasChildren && childrenContainer) {
            toggleNode(toggleEl, childrenContainer);
        } else if (!hasChildren && !state.loadedNodes.has(node.id)) {
            lazyLoadChildren(node, itemEl, toggleEl, depth);
        }
    }

    function toggleNode(toggleEl, childrenContainer) {
        const expanded = childrenContainer.classList.toggle('expanded');
        toggleEl.classList.toggle('expanded', expanded);
    }

    function lazyLoadChildren(node, itemEl, toggleEl, depth) {
        if (!state.currentBook) return;
        state.loadedNodes.add(node.id);

        const paras = {
            galPath: state.currentBook.path,
            subPath: 'Book',
            varData: '_templetData',
            parentId: parseInt(node.id.startsWith('F') ? node.id.substring(1) : node.id, 10),
            userId: '0',
        };

        const loadingEl = document.createElement('div');
        loadingEl.className = 'loading';
        loadingEl.innerHTML = '<div class="spinner" style="width:16px;height:16px;border-width:2px"></div>';
        loadingEl.style.padding = '4px 16px';
        itemEl.parentNode.appendChild(loadingEl);

        callApi('GetChildDrawingDir', paras)
            .then(({ data }) => {
                loadingEl.remove();
                if (data && data.length > 0) {
                    node.children = data;

                    let childrenContainer = itemEl.parentNode.querySelector('.tree-children');
                    if (!childrenContainer) {
                        childrenContainer = document.createElement('div');
                        childrenContainer.className = 'tree-children';
                        itemEl.parentNode.appendChild(childrenContainer);
                    }
                    const currentDepth = depth || ((parseInt(itemEl.style.paddingLeft) - 8) / 16) + 1;
                    data.forEach((child) => {
                        childrenContainer.appendChild(createTreeNode(child, currentDepth));
                    });
                    childrenContainer.classList.add('expanded');
                    if (toggleEl) {
                        toggleEl.classList.remove('no-children');
                        toggleEl.classList.add('expanded');
                        toggleEl.innerHTML =
                            '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="9 18 15 12 9 6"></polyline></svg>';
                    }
                    const icon = itemEl.querySelector('.tree-icon');
                    if (icon) icon.textContent = '📁';
                }
            })
            .catch((err) => {
                loadingEl.remove();
                console.warn('懒加载子节点失败:', err.message);
            });
    }

    // ============================================================
    // 内容加载
    // ============================================================

    function loadContent(itempath, itemcontent, word) {
        if (!state.currentBook) return;
        const gal = state.currentBook.path;
        let url = `${CONTENT_PAGE}?gal=${encodeURIComponent(gal)}&path=${encodeURIComponent(itempath)}&content=${encodeURIComponent(itemcontent)}`;
        if (word) {
            url += `&word=${encodeURIComponent(word)}`;
        }
        dom.iframe.src = url;
        showContentEmpty(false);
    }

    function showContentEmpty(show) {
        if (show) {
            dom.contentEmpty.classList.remove('hidden');
            dom.iframe.src = '';
        } else {
            dom.contentEmpty.classList.add('hidden');
        }
    }

    // ============================================================
    // 上一页 / 下一页
    // ============================================================

    function gotoNextPage(direction) {
        if (!state.currentBook || !state.selectedNodeId) return;

        const paras = {
            galPath: state.currentBook.path,
            subPath: 'Book',
            varData: '_templetData',
            id: $('hidActiveDirId').value,
            next: direction,
            userId: '0',
        };

        callApi('GetNextContent', paras)
            .then(({ data }) => {
                if (data && data.length > 0) {
                    const obj = data[0];
                    const numericId = obj.id.startsWith('F') ? obj.id.substring(1) : obj.id;
                    $('hidActiveDirId').value = numericId;
                    state.selectedNodeId = obj.id;

                    document.querySelectorAll('.tree-item.selected').forEach((el) => el.classList.remove('selected'));
                    const nodeEl = dom.treeContainer.querySelector(`[data-node-id="${obj.id}"] > .tree-item`);
                    if (nodeEl) {
                        nodeEl.classList.add('selected');
                        nodeEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                    }
                    loadContent(obj.itempath, obj.itemcontent, dom.txtFind.value);
                }
            })
            .catch((err) => console.error('翻页失败:', err));
    }

    // ============================================================
    // 搜索
    // ============================================================

    function doSearch() {
        if (!state.currentBook) {
            alert('请先选择一本书');
            return;
        }
        const keyword = dom.txtFind.value.trim();
        if (!keyword) {
            alert('请输入搜索关键词');
            return;
        }

        switchTab('search');

        dom.searchResults.innerHTML = `
            <div class="loading">
                <div class="spinner"></div>
                <span>搜索中...</span>
            </div>`;

        const paras = {
            galPath: state.currentBook.path,
            subPath: 'Book',
            varData: '_templetData',
            find: keyword,
            userId: '0',
        };

        setTimeout(() => {
            callApi('QueryContent', paras)
                .then(({ data }) => renderSearchResults(data || [], keyword))
                .catch((err) => {
                    dom.searchResults.innerHTML =
                        '<div class="empty-state"><p>搜索失败</p><p style="font-size:12px">' + err.message + '</p></div>';
                });
        }, 200);
    }

    function renderSearchResults(results, keyword) {
        if (results.length === 0) {
            dom.searchResults.innerHTML = `
                <div class="empty-state">
                    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                        <circle cx="11" cy="11" r="8"></circle>
                        <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                    </svg>
                    <p>未找到与"${escapeHtml(keyword)}"相关的内容</p>
                </div>`;
            return;
        }

        let html = `<div class="search-result-count">找到相关结果 ${results.length} 条</div>`;
        results.forEach((item) => {
            const numericId = item.id.startsWith('F') ? item.id.substring(1) : item.id;
            html += `
                <div class="search-result-item" data-id="${item.id}" data-numeric-id="${numericId}" data-path="${escapeAttr(item.itempath)}" data-content="${escapeAttr(item.itemcontent)}">
                    ${escapeHtml(item.text)}
                </div>`;
        });
        dom.searchResults.innerHTML = html;

        dom.searchResults.querySelectorAll('.search-result-item').forEach((el) => {
            el.addEventListener('click', () => {
                state.selectedNodeId = el.dataset.id;
                $('hidActiveDirId').value = el.dataset.numericId;
                document.querySelectorAll('.tree-item.selected').forEach((n) => n.classList.remove('selected'));
                const nodeEl = dom.treeContainer.querySelector(`[data-node-id="${el.dataset.id}"] > .tree-item`);
                if (nodeEl) nodeEl.classList.add('selected');
                loadContent(el.dataset.path, el.dataset.content, dom.txtFind.value);
            });
        });
    }

    // ============================================================
    // 侧边栏
    // ============================================================

    function switchTab(tabName) {
        dom.tabs.forEach((tab) => {
            tab.classList.toggle('active', tab.dataset.tab === tabName);
        });
        dom.panels.forEach((panel) => {
            panel.classList.toggle('active', panel.id === 'panel' + tabName.charAt(0).toUpperCase() + tabName.slice(1));
        });
    }

    function toggleSidebar() {
        dom.sidebar.classList.toggle('collapsed');
    }

    function initResizer() {
        let isResizing = false, startX = 0, startWidth = 0;
        dom.resizer.addEventListener('mousedown', (e) => {
            isResizing = true;
            startX = e.clientX;
            startWidth = dom.sidebar.offsetWidth;
            dom.resizer.classList.add('dragging');
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
            e.preventDefault();
        });
        document.addEventListener('mousemove', (e) => {
            if (!isResizing) return;
            const newWidth = Math.max(200, Math.min(600, startWidth + e.clientX - startX));
            dom.sidebar.style.width = newWidth + 'px';
        });
        document.addEventListener('mouseup', () => {
            if (isResizing) {
                isResizing = false;
                dom.resizer.classList.remove('dragging');
                document.body.style.cursor = '';
                document.body.style.userSelect = '';
            }
        });
    }

    // ============================================================
    // 工具函数
    // ============================================================

    function escapeHtml(str) {
        if (!str) return '';
        return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function escapeAttr(str) {
        if (!str) return '';
        return String(str).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // ============================================================
    // 事件绑定
    // ============================================================

    function bindEvents() {
        dom.cmbList.addEventListener('change', () => {
            const id = parseInt(dom.cmbList.value, 10);
            const book = state.bookList.find((b) => b.id === id);
            if (book) selectBook(book);
        });
        dom.btnQuery.addEventListener('click', doSearch);
        dom.txtFind.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') doSearch();
        });
        dom.hrefPrvPage.addEventListener('click', () => gotoNextPage('-1'));
        dom.hrefNextPage.addEventListener('click', () => gotoNextPage('1'));
        dom.tabs.forEach((tab) => {
            tab.addEventListener('click', () => switchTab(tab.dataset.tab));
        });
        dom.sidebarToggle.addEventListener('click', toggleSidebar);
        initResizer();
        dom.iframe.addEventListener('load', () => {
            if (dom.iframe.src) showContentEmpty(false);
        });
    }

    // ============================================================
    // 初始化
    // ============================================================

    function init() {
        bindEvents();
        loadBookList();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
