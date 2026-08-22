# Novel KG Studio

把整篇长篇小说拆成知识图谱（人物/线索/地点/时间/事件节点，关系用线相连），
提供图+词法混合 RAG 检索（题目 -> 一阶线索 -> 二阶扩散线索），
并生成三维动态可视化 dashboard（HTML，浏览器直接打开）。

## 流程

1. **第一遍 LLM**：遍历全文，剔除文学性内容，保留情节相关句子并按时间排序。
2. **第二遍 LLM**：对时间排序后的文本建知识图谱（实体 + 关系，证据逐字校验）。
3. **RAG**：BM25 索引节点文本 + 图谱一阶/二阶扩散检索。
4. **可视化**：删减条、两张尺子密度图、三个 3D 视图、RAG 检索 3D 子图。

## 安装

```bash
uv venv .venv
uv pip install -p .venv -e ".[dev]"
```

## 运行

```bash
$env:DEEPSEEK_API_KEY="sk-..."
$env:DEEPSEEK_BASE_URL="https://api.deepseek.com"
.venv\Scripts\python.exe -m novel_kg_studio.demo.run_all
```

输出到 `outputs/demo/dashboard.html`。

## 测试

```bash
.venv\Scripts\python.exe -m pytest -q
```

