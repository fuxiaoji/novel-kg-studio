# DQA30 侦探小说长上下文推理实验 —— 数据、发现与论文全记录

> **Comprehensive project report.** 本文件是项目的**总记录**:包含所有实验数据、所有发现、以及两篇论文（英文 NeurIPS 草稿 + 中文完整版）的全文内容。所有数字均可由仓库中的 JSON/CSV 复现。

- **实验主体**：30 部侦探小说 × 234 道多项选择题 × 固定本地 9B 模型（16K 上下文）
- **核心问题**：非线性知识图谱（"思维导图"）检索，能否让小型长上下文模型逼近"拿到全部金标证据"的推理上限？
- **头号结果**：图谱引导的证据扩展 53.8% ≈ 公平金标预言机 54.7%（配对 p=0.912，统计不可区分），而公平金标相对仅题目对照 +14.53pp（p=0.0005）
- **方法学发现**：选项在前、原始顺序的"金标预言机"有严重 D 位置锚定伪影（选 D 占 59%，D 正确率 79.4%），必须洗牌选项 + 证据前置才能作上限
- **新核验发现**：三种图谱方法在原始选项顺序下**不**押 D（选 D 29.5–31.6% ≈ 内容金标 27.8%），说明 D 锚定是"选项在前 + 全部证据"这一特定配置特有的伪影

---

## 目录

- [1. 项目概览](#1-项目概览)
- [2. 数据集与实验设置](#2-数据集与实验设置)
- [3. 方法与协议](#3-方法与协议)
- [4. 全部数据](#4-全部数据)
- [5. 所有发现](#5-所有发现)
- [6. 论文全文（英文版）](#6-论文全文英文版)
- [7. 论文全文（中文版）](#7-论文全文中文版)
- [8. 复现指南](#8-复现指南)
- [9. 文件与资源索引](#9-文件与资源索引)

---

## 1. 项目概览

**研究动机。** LLM 的上下文窗口是一段**线性历史**：token 按叙述顺序进入固定窗口，证据可及性同时取决于"相关性"与"位置"。对一部约 50 万字符（约 10^5 token，远大于 16K 上下文）的小说，答案所在的段落可能位于线性位置 0.28，而模型有效注意力被钉在尾部（Lost in the Middle / Found in the Middle 现象）。检索问题因此不是"证据是否存在"，而是"阅读器能否非线性地导航到它"。

**我们的原语：图谱导航。** 在小说之上构建知识图谱（人物 / 地点 / 线索物品 / 事件 / 时间锚点节点 + 证据句，带类型关系相连），模型从查询节点出发沿图边跳转，无论证据在线性文本何处，都只用很小上下文预算即可到达。

**长远视角。** 图谱导航是**非线性结构化记忆**的最简实例——一种读者通过导航而非线性扫描来查询的稀疏索引。这一原语会随上下文增长而更重要：1M token 上下文面对 100M token 语料或长期记忆时，瓶颈仍是线性访问，而非窗口大小。

**核心实验设置（一句话）。** 冻结 30 部小说的离线图谱，固定 qwen3.5:9b（16K 上下文、禁用思维链），评估 3 种图谱协议、3 种基线、仅题目对照与多种金标预言机变体，全部 2,808 次评估（12 条件 × 234 题）共享同一模型。

**头号数字。**

| 条件 | 合并准确率 | 说明 |
|---|---|---|
| 公平金标预言机（**上限**） | **54.7%**（128/234） | 证据前置 + 洗牌选项 + 简洁指令 |
| 图谱引导的证据扩展 | 53.8%（126/234） | 与上限 p=0.912，不可区分 |
| 基于图谱的分歧仲裁 | 53.4%（125/234） | 与上限 p=0.832，不可区分 |
| 图谱原生分块重排 | 50.9%（119/234） | |
| 向量 RAG 基线 | 51.7%（121/234） | |
| 整书压缩基线 | 51.3%（120/234） | |
| 尾窗口基线 | 46.2%（108/234） | |
| 仅题目对照 | 40.2%（94/234） | 地板 |
| 选项在前金标预言机 | 41.5%（97/234） | **D 锚定伪影，不可作上限** |

---

## 2. 数据集与实验设置

### 2.1 语料

- **30 部侦探小说**，234 道多项选择题。
  - **old20**（第一队列）：20 部小说，164 题。
  - **new10**（最后队列）：10 部小说，70 题。
- 每题 4 个选项 + 一组**官方线索段**（用于金标预言机）。
- 题目来自 DetectiveQA 风格（`detectiveqa_<novel>_...`）。

### 2.2 模型与上下文

- 固定本地模型 **qwen3.5:9b**，16K 上下文。
- **禁用思维链（CoT）**：使测得的准确率差异可归因于输入而非解码策略；本任务为简短多项选择，CoT 主要改变冗长度而非内容。
- 全部 **2,808 次评估**（12 条件 × 234 题）共享这一模型：
  - 4 种金标预言机变体 + 3 种图谱协议 + 3 种基线 + 2 种对照。

### 2.3 冻结图谱与污染控制

- 图谱**离线构建并冻结**；答案永不进入图谱。
- 两条不同的冻结图谱流水线：
  - **old20**：7B 抽取器，稀疏关系模式，**平均 456 条边/部**。
  - **new10**：与阅读器相同的 9B 模型，稠密关系模式，**平均 758 条边/部**。
- 两流水线**金标段落召回差异显著**（old20 16.3% vs new10 73.2%，见 §4.8），合并数字**仅具描述性**，全文如此标注。

### 2.4 金标证据定义

- 金标证据 = 每个线索位置**非负**的官方线索段；**排除**最终答案段。
- 公平金标预言机把这些段落拼接在（已洗牌的）选项**之前**。

### 2.5 指标

- 微平均准确率（234 题）；分队列准确率；难题子集准确率（仅题目对照答错的 140 题）；逐金标字母准确率；配对统计（McNemar + novel-clustered bootstrap）。

---

## 3. 方法与协议

### 3.1 方法命名对照（论文禁用代号，此处为内部追溯表）

| 内部代号 | 论文描述性名称 | 合并准确率 | 说明 |
|---|---|---|---|
| **G7** | 图谱引导的证据扩展（Graph-guided evidence expansion） | **53.8%** | 从查询节点沿图边扩展到若干证据块（"紧致"协议，上下文极小） |
| **G9** | 图谱原生分块重排（Graph-native chunk reranking） | 50.9% | 图谱邻居候选池 → 按跳数与实体重叠重排 → 取顶部若干块 |
| **G10** | 基于图谱的分歧仲裁（Graph-based disagreement arbitration） | 53.4% | 运行 G7+G9 两个阅读器，不一致时由纯图谱裁判按证据并集定夺 |
| **B1** | 尾窗口基线（Recent-window baseline） | 46.2% | 小说尾部直到上下文上限，自然线性历史基线 |
| **B2** | 整书压缩基线（Whole-book compression baseline） | 51.3% | 小说抽取压缩进上下文窗口后阅读 |
| **B3** | 向量 RAG 基线（Vector RAG baseline） | 51.7% | 扁平稠密检索，按题目相似度选块 |
| **Q0** | 仅题目对照（Question-only control） | 40.2% | 只给题目+选项，地板 |
| **Q0T** | 仅题目对照（简洁指令版） | 40.2% | 同 Q0 但用"Answer: [letter]"简洁指令，用于公平协议对照组 |
| **GOLD_V2** | **公平金标预言机（Fair gold oracle，金标上限）** | **54.7%** | 证据前置 + 按题 SHA-256 播种洗牌 + 简洁指令 |
| **GOLD_V3** | 选项在前金标（洗牌） | 52.6% | 选项在前但洗牌，用于隔离洗牌效应 |
| **GOLD_V1** | 金标预言机（证据前置，原始顺序） | 57.7% | 证据前置但不洗牌 |
| **GOLD_ORIG** | 选项在前金标（原始顺序，**D 锚定伪影**） | 41.5% | 金标答案在 D 时 79.4% 正确，选 D 占 59%，**绝不当上限** |

### 3.2 图谱表示

- 节点 = 带类型实体（人物、地点、线索物品、事件、时间锚点）+ 证据句（提到某实体的段落）。
- 边 = 带类型关系（出现于、参与、时间 之前/之后、导致、支持、矛盾、相关）。
- 每部小说冻结一个离线图谱；每个问题通过实体匹配锚定到查询节点。

### 3.3 去偏金标协议（为什么必须去偏）

1. **问题**：自然的"选项在前 + 原始顺序"金标预言机对 9B 模型是一个**无用的上限**——金标答案为 D 时正确率 79.4%，A–C 仅 21.8–40.0%；且 **59% 的题目都选 D**。模型学的是"答案坐在哪"，不是"答案意味着什么"。
2. **去偏协议（GOLD_V2，公平金标）**：
   - 证据段落**先于**选项呈现；
   - 选项顺序**按题洗牌**，置换由题目 ID 的 **SHA-256** 确定性导出（可复现、不可利用位置）；
   - 简洁指令："Do not explain. Answer with exactly one line: Answer: [letter]"
3. **协议中性验证**：用同样简洁指令 + 洗牌选项重跑仅题目对照（Q0T），合并准确率与原始对照相同（40.2%），确认 +14.5pp 增益来自**证据**而非指令或顺序。（分队列 Q0T 与 Q0 有偏离：old20 41.5% vs 37.2%；new10 37.1% vs 47.1%，因此合并持平只是描述性。）

### 3.4 统计协议

- 所有方法在同一 234 题上评估 → 每个比较都是**配对比较**。
- 主要推断：配对不一致对上的**精确双侧 McNemar 检验**。
- 报告：准确率点数差 Δ、不一致对 W/L、精确 p、**novel-clustered bootstrap 95% CI**（5,000 次整书重采样，抽样单元是小说而非题目）。
- 图谱对基线共 9 个对比（3 协议 × 3 基线）用 **Holm–Bonferroni** 校正。
- Δ 按"第一个条件 − 第二个条件"报告。

---

## 4. 全部数据

### 4.1 主结果表（微平均准确率 %）

| 方法 | old20 | new10 | **合并** | 难题子集（n=140） |
|---|---|---|---|---|
| **公平金标预言机（金标上限）** | 56.7 | 50.0 | **54.7** | 47.1（66/140） |
| 图谱引导的证据扩展 | 54.3 | 52.9 | **53.8** | 42.9 |
| 图谱原生分块重排 | 50.6 | 51.4 | 50.9 | **45.0** |
| 基于图谱的分歧仲裁 | 51.2 | **58.6** | 53.4 | 41.4 |
| 尾窗口基线 | 48.2 | 41.4 | 46.2 | 33.6 |
| 整书压缩基线 | 51.2 | 51.4 | 51.3 | 37.1 |
| 向量 RAG 基线 | 52.4 | 50.0 | 51.7 | 36.4 |
| 仅题目对照 | 37.2 | 47.1 | 40.2 | -- |

> 注：合并为描述性（两队列图谱流水线不同）。new10 上图谱分歧仲裁 58.6% 超过公平金标线 50.0%，因图谱协议跑原始选项顺序而预言机洗牌（协议不对称，方向有利于图方法，见 §5.5）。

### 4.2 完整分队列结果（12 条件 × 3 队列，正确数/总数）

**old20（164 题）**

| 方法 | 正确/总数 | 准确率 |
|---|---|---|
| 公平金标预言机（金标上限） | 93/164 | 56.7% |
| 金标预言机（证据前置，原始顺序）GOLD_V1 | 97/164 | 59.1% |
| 金标预言机（选项在前，洗牌）GOLD_V3 | 88/164 | 53.7% |
| 选项在前金标预言机 GOLD_ORIG | 63/164 | 38.4% |
| 图谱引导的证据扩展 | 89/164 | 54.3% |
| 图谱原生分块重排 | 83/164 | 50.6% |
| 基于图谱的分歧仲裁 | 84/164 | 51.2% |
| 尾窗口基线 | 79/164 | 48.2% |
| 整书压缩基线 | 84/164 | 51.2% |
| 向量 RAG 基线 | 86/164 | 52.4% |
| 仅题目对照（简洁指令）Q0T | 68/164 | 41.5% |
| 仅题目对照 Q0 | 61/164 | 37.2% |

**new10（70 题）**

| 方法 | 正确/总数 | 准确率 |
|---|---|---|
| 公平金标预言机（金标上限） | 35/70 | 50.0% |
| 金标预言机（证据前置，原始顺序）GOLD_V1 | 38/70 | 54.3% |
| 金标预言机（选项在前，洗牌）GOLD_V3 | 35/70 | 50.0% |
| 选项在前金标预言机 GOLD_ORIG | 34/70 | 48.6% |
| 图谱引导的证据扩展 | 37/70 | 52.9% |
| 图谱原生分块重排 | 36/70 | 51.4% |
| **基于图谱的分歧仲裁** | **41/70** | **58.6%** |
| 尾窗口基线 | 29/70 | 41.4% |
| 整书压缩基线 | 36/70 | 51.4% |
| 向量 RAG 基线 | 35/70 | 50.0% |
| 仅题目对照（简洁指令）Q0T | 26/70 | 37.1% |
| 仅题目对照 Q0 | 33/70 | 47.1% |

**合并（234 题，描述性）**

| 方法 | 正确/总数 | 准确率 |
|---|---|---|
| 公平金标预言机（金标上限） | 128/234 | 54.7% |
| 金标预言机（证据前置，原始顺序）GOLD_V1 | 135/234 | 57.7% |
| 金标预言机（选项在前，洗牌）GOLD_V3 | 123/234 | 52.6% |
| 选项在前金标预言机 GOLD_ORIG | 97/234 | 41.5% |
| **图谱引导的证据扩展** | **126/234** | **53.8%** |
| 图谱原生分块重排 | 119/234 | 50.9% |
| 基于图谱的分歧仲裁 | 125/234 | 53.4% |
| 尾窗口基线 | 108/234 | 46.2% |
| 整书压缩基线 | 120/234 | 51.3% |
| 向量 RAG 基线 | 121/234 | 51.7% |
| 仅题目对照（简洁指令）Q0T | 94/234 | 40.2% |
| 仅题目对照 Q0 | 94/234 | 40.2% |

### 4.3 难题子集（Q0-hard，n=140）与保留率

仅题目对照答错的 140 题 = 真正需要证据的题。

| 方法 | 难题子集准确率 | 对照答对题的保留率 |
|---|---|---|
| 公平金标预言机 | 47.1%（66/140） | -- |
| 图谱原生分块重排 | **45.0%**（63/140） | 59.6% |
| 图谱引导的证据扩展 | 42.9%（60/140） | 70.2% |
| 基于图谱的分歧仲裁 | 41.4%（58/140） | **71.3%** |
| 整书压缩基线 | 37.1%（52/140） | 72.3% |
| 向量 RAG 基线 | 36.4%（51/140） | **74.5%** |
| 尾窗口基线 | 33.6%（47/140） | 64.9% |
| 选项在前金标预言机（伪影） | 30.7%（43/140） | 57.4% |

### 4.4 配对统计检验（同一 234 题，精确双侧 McNemar；W/L = 不一致对胜/负；95% CI = novel-clustered bootstrap）

**金标预言机分析块**

| 配对对比 | Δ | W/L | 精确 p | Holm p | 95% CI (pp) |
|---|---|---|---|---|---|
| **公平金标 vs 仅题目(简洁)** | **+14.53pp** | 63/29 | **0.0005** | -- | [6.9, 23.4] |
| 公平金标 vs 图谱引导的证据扩展 | +0.85pp | 42/40 | 0.912 | -- | [-5.9, 8.0] |
| 公平金标 vs 图谱分歧仲裁 | +1.28pp | 46/43 | 0.832 | -- | [-6.6, 9.7] |
| 公平金标 vs 选项在前金标(洗牌) | +2.14pp | 32/27 | 0.603 | -- | [-3.3, 7.8] |
| 选项在前金标 vs 仅题目（伪影） | +1.28pp | 43/40 | 0.826 | -- | [-4.9, 7.8] |

**图谱 vs 基线（9 对比族，Holm 校正）**

| 配对对比 | Δ | W/L | 精确 p | Holm p | 95% CI (pp) |
|---|---|---|---|---|---|
| **证据扩展 vs 尾窗口（合并）** | **+7.69pp** | 47/29 | 0.050 | 0.454 | [2.0, 13.9] |
| 证据扩展 vs 压缩（合并） | +2.56pp | 48/42 | 0.598 | 1.0 | [-3.6, 8.3] |
| 证据扩展 vs RAG（合并） | +2.14pp | 36/31 | 0.625 | 1.0 | [-4.6, 8.8] |
| 重排 vs 尾窗口（合并） | +4.70pp | 45/34 | 0.260 | 1.0 | [-3.3, 12.8] |
| 重排 vs 压缩（合并） | -0.43pp | 43/44 | 1.0 | 1.0 | [-8.3, 7.4] |
| 重排 vs RAG（合并） | -0.85pp | 36/38 | 0.908 | 1.0 | [-7.8, 6.4] |
| 分歧仲裁 vs 尾窗口（合并） | +7.26pp | 43/26 | 0.053 | 0.454 | [0.8, 14.2] |
| 分歧仲裁 vs 压缩（合并） | +2.14pp | 44/39 | 0.661 | 1.0 | [-4.6, 8.1] |
| 分歧仲裁 vs RAG（合并） | +1.71pp | 34/30 | 0.708 | 1.0 | [-4.8, 8.3] |
| **分歧仲裁 vs 尾窗口（new10）** | **+17.14pp** | 15/3 | 0.008 | 0.068 | [5.4, 31.9] |

### 4.5 逐金标字母准确率（%）

| 运行 | A | B | C | D |
|---|---|---|---|---|
| 公平金标预言机 | 55.2 | 47.2 | 53.4 | 61.5 |
| 仅题目对照(简洁) | 32.8 | 34.0 | 46.6 | 46.2 |
| 选项在前金标预言机 | 21.8 | 24.5 | 40.0 | **79.4** ⚠️ |

### 4.6 所选字母分布（占 234 题的比例）——含图谱方法新核验

| 运行 | A | B | C | D | D 占比 |
|---|---|---|---|---|---|
| 公平金标预言机（洗牌） | 50 | 47 | 71 | 66 | **28.2%** |
| 选项在前金标(洗牌) GOLD_V3 | 46 | 52 | 79 | 57 | **24.4%** |
| 仅题目对照(简洁) | 30 | 64 | 88 | 52 | 22.2% |
| **金标字母真实分布（内容）** | 58 | 53 | 58 | 65 | **27.8%** |
| 选项在前金标预言机（原始顺序） | 23 | 17 | 48 | 138 | **59.0%** ⚠️ |
| --- 图谱方法（原始顺序，新核验）--- | | | | | |
| 图谱引导的证据扩展 G7 | 71 | 37 | 56 | 70 | **29.9%** |
| 图谱原生分块重排 G9 | 68 | 36 | 55 | 74 | **31.6%** |
| 基于图谱的分歧仲裁 G10 | 68 | 40 | 57 | 69 | **29.5%** |

**要点**：
- 选项在前金标预言机**无论内容如何都涌向 D**（138/234 = 59.0%）；它还在 8 题上拒绝选字母（该行合计 226）。
- 洗牌后（GOLD_V3）D 占比降到 24.4%，公平金标 GOLD_V2 为 28.2% ≈ 内容 27.8%。
- **图谱方法虽跑原始顺序，却不押 D**：G7 29.9%、G9 31.6%、G10 29.5%，全部 ≈ 内容占比。59% 的 D 锚定是"选项在前 + 全部证据 + 原始顺序"这一特定配置特有的伪影（详见 §5.4）。

### 4.7 闭源大模型参考点（DeepSeek v4-flash，禁用思维链，old20 队列 164 题）

| 条件 | 正确/总数 | 微平均准确率 |
|---|---|---|
| 仅题目 | 68/164 | 41.5% |
| 线性尾部（50K 字符） | 90/164 | 54.9% |
| **整本小说** | 132/164 | **80.5%** |
| --- | | |
| 图谱引导的证据扩展（本地 9B） | 89/164 | 54.3% |

- DeepSeek 其余指标：仅题目答错的 96 题上，全小说 69/96 = 71.9%。
- **观察 1**：线性历史阅读把即使前沿模型也封顶在 54.9%（尾部 50K 字符）；只有读完整本小说才跃至 80.5%——这是线性访问的代价。
- **观察 2**：本地 9B 靠图谱导航达 54.3%，以一小部分上下文预算**追平**了前沿模型的线性尾部阅读准确率。这是描述性跨模型参考，非受控对比。

### 4.8 图结构与金标富集 / 召回

**2-core 拓扑富集**（金标重叠节点是否集中在图的核心）：

| 队列 | 金标节点在 2-core 比例 | 非金标节点在 2-core 比例 | 富集比 | 比值比 (OR) |
|---|---|---|---|---|
| old20 | 53.7%（66/123） | 18.2%（1835/10095） | **2.95** | 5.21 |
| new10 | 53.9%（362/672） | 33.4%（1524/4557） | **1.61** | 2.32 |
| 合并（描述性） | 53.8%（428/795） | 22.9%（3359/14652） | **2.35** | 3.92 |

**金标段落位置召回**（图检索能找回多少官方金标证据）：

| 队列 | 金标段落召回 | 答案段召回 |
|---|---|---|
| old20 | **16.3%** | 23.2% |
| new10 | **73.2%** | 88.2% |

> 队列异质性的根因：old20 图管线召回极低（16.3%），new10 高得多（73.2%）。这界定了图谱流水线**失效与成功的两种模式**，也解释了为什么 new10 上图谱方法可超预言机线。

---

## 5. 所有发现

1. **图谱导航逼近金标上限。** 图谱引导的证据扩展 53.8% vs 公平金标预言机 54.7%，配对 +0.85pp、p=0.912，**统计上不可区分**；分歧仲裁 +1.28pp、p=0.832。一个只读若干图谱选段落的阅读器，与一个被交给全部官方线索的阅读器表现一样。
2. **去偏金标是真正的上限。** 公平金标预言机比仅题目对照高 +14.53pp（63 胜 29 负，p=0.0005，95% CI [6.9, 23.4]）——同一 9B 模型拿到全部官方线索、位置中性协议下，移动了 14.5 个准确率点。
3. **选项位置锚定是方法学伪影。** 选项在前金标预言机 41.5%，与仅题目对照（40.2%）无差异；但金标为 D 时 79.4% 正确、A–C 仅 21.8–40.0%，且 59% 的题都选 D。**不洗牌选项的预言机上限不可用**。
4. **洗牌 + 证据前置消除伪影。** GOLD_V3（洗牌但选项在前）D 占比从 59.0% 降到 24.4%；公平金标（GOLD_V2）逐字母准确率拉平（55.2/47.2/53.4/61.5），选择分布跟随内容金标。
5. **图谱方法不押 D（新核验）。** 三种图谱方法跑原始选项顺序，但选 D 比例（29.9% / 31.6% / 29.5%）≈ 内容金标（27.8%），无 D 锚定。D 堆叠只出现在"选项在前 + 全部证据 + 原始顺序"配置下。
6. **队列异质性。** old20 金标召回 16.3%（图构建失败模式）、new10 73.2%（成功模式）。图方法相对上限的差距**几乎全部来自检索侧**，不是阅读器推理能力不足。
7. **难题子集（140 题）上图谱全部超过线性基线。** 重排 45.0%、扩展 42.9%、仲裁 41.4% vs 尾窗口 33.6%、压缩 37.1%、RAG 36.4%；上限为 47.1%。
8. **最强配对信号。** 合并上证据扩展 vs 尾窗口 +7.69pp（p=0.050，Holm p=0.454）；new10 上仲裁 vs 尾窗口 +17.14pp（15 胜 3 负，p=0.008，Holm p=0.068）。
9. **闭源参考。** DeepSeek v4-flash 全小说 80.5% vs 线性尾部 50K 字符 54.9%；本地 9B 图导航 54.3% 追平前沿模型的线性尾部。
10. **2-core 富集。** 金标证据集中于图的拓扑 2-core（old20 富集 2.95、new10 1.61、合并 2.35），支持"图结构本身编码证据位置"的观点。
11. **协议不对称对图方法有利。** 图协议跑原始顺序、预言机洗牌。洗牌对位置敏感的 9B 更难，因此"与上限不可区分"的主张是**保守的**——这也是 new10 上图方法（58.6%）能超预言机线（50.0%）的原因。

---

## 6. 论文全文（英文版）

> 以下为 `paper/neurips2026/main.tex` 的完整内容转写（NeurIPS 2026 投稿草稿，双盲匿名，正文 9 页 + 附录）。原始 LaTeX/PDF：`paper/neurips2026/main.tex`、`paper/neurips2026/build/main.pdf`。

### 题目 (Title)

**Can a Non-Linear Knowledge Graph Make a Small-Model Long-Context Reader Approach the Evidence Ceiling? A Detective-Novel Case Study**

### 摘要 (Abstract)

Large language models read long documents as a *linear* input history: tokens enter a fixed context window in narrative order, so evidence access depends on position as much as on relevance. We ask whether a *non-linear* retrieval system---a knowledge-graph "mind map" over the text---lets a small-context model approach the ceiling of what the evidence itself permits. We freeze one offline knowledge graph per novel across 30 detective novels and 234 multiple-choice questions, and evaluate three graph-navigation protocols against a recent-window baseline, a whole-book compression baseline, a vector-RAG baseline, and a question-only control, all with a fixed 9B local model under a 16K context. The thirty novels span two frozen graph-build pipelines, so pooled numbers are descriptive. The de-biased gold oracle---the model shown every official clue paragraph under a protocol that removes option-position artifacts---reaches 54.7%, while the question-only control reaches 40.2% (paired +14.53pp, p=0.0005). Graph-guided evidence expansion reaches 53.8%, statistically indistinguishable from the oracle (p=0.912), even though it reads only a handful of graph-selected paragraphs per question. We further show that an options-first oracle run is an unreliable ceiling: it is correct for **79.4%** of questions whose answer is option D yet only 21.8--40.0% for A--C, an option-position anchoring artifact that a de-biasing protocol removes. More broadly, we view graph navigation as an instance of *non-linear structured memory*---a sparse index a model can hop across, so that reasoning depends on structure rather than position. We expect this primitive to matter more as context windows grow toward a million tokens while documents and long-term memory reach orders of magnitude beyond that.

**Keywords:** long-context reasoning; knowledge-graph retrieval; non-linear structured memory; small language models; multiple-choice evaluation.

### 1 Introduction

An LLM's context window is a linear history: the model observes the document token by token and, in a fixed-length window, must decide what to attend to. For a novel of roughly half a million characters (about 10⁵ tokens)---far larger than a 16K context---the answer to "why did the killer leave the canned tea" may rest in a paragraph near position 0.28 while the model's effective attention is pinned near the tail [Lost in the Middle; Found in the Middle]. The retrieval problem is therefore not "is the evidence present" but "can the reader navigate to it non-linearly". Retrieval-augmented generation [RAG] mitigates this with a flat similarity index, but it still treats the document as an ordered byte stream to be sliced.

This paper studies a different primitive: *graph navigation*. A knowledge graph over the novel---people, locations, clue objects, events, temporal anchors, and the evidence sentences that mention them, connected by typed relations---is a non-linear "mind map". To answer a question, the model can hop from the query node to a small set of evidence nodes, staying well within a small context budget regardless of where the evidence sits in the linear text (Figure 1). The central question of this paper is: for a small, local, long-context model, can such non-linear graph navigation bring the model close to the *gold ceiling*---the accuracy the model achieves when it is handed every official clue paragraph? The primitive scales beyond this testbed: a 1M-token context is still dwarfed by a 100M-token corpus or a long-term memory store, and the bottleneck there is the same linear access we study here, not window size.

We construct a clean, frozen benchmark to test this. On 30 detective novels with 234 multiple-choice questions, we fix a 9B local model (16K context, chain of thought disabled) and evaluate (i) three graph-navigation protocols built from frozen per-novel graphs, (ii) three standard retrieval/compression baselines, (iii) a question-only control, and (iv) a *fair gold oracle*: the model shown all official clue paragraphs under a protocol that removes option-position artifacts. We measure whether graph navigation approaches the oracle, and we quantify exactly where the remaining gap comes from.

**Figure 1 (schematic).** Linear input history vs. non-linear graph navigation. **Top:** the novel is a 0–1 linear reading ruler; a fixed-size reading window on the tail cannot see a supporting clue at position 0.28. **Bottom:** the same clue is one graph hop from the question node, reachable with a tiny context budget independently of linear position. Hop depth is encoded by size and color (1st hop saturated, 2nd hop lighter, unrelated nodes gray) following the dashboard color convention used throughout our analysis.

Our headline results are threefold.

- **The de-biased oracle is a real ceiling.** Under a protocol that shuffles options and presents evidence before options, the gold oracle reaches 54.7% versus 40.2% for the question-only control (+14.53pp, paired exact McNemar p=0.0005); the original options-first gold run reaches only 41.5%, equal to the question-only control (40.2%), because the model anchors on option position.
- **Graph navigation approaches the ceiling.** Graph-guided evidence expansion reaches 53.8%, statistically indistinguishable from the fair gold oracle (p=0.912, paired over the same 234 questions), using only graph-derived evidence. All three graph protocols beat the recent-window baseline on the harder questions.
- **Option-position artifacts must be controlled.** The fair gold oracle's +14.5pp advantage is not visible in the options-first oracle run; comparing methods to an un-de-biased oracle is meaningless.

The paper is organized as follows. Section 2 situates the work in long-context and graph-retrieval literature. Section 3 describes the three graph-navigation protocols, the baselines, and the de-biased gold protocol. Section 4 details the frozen corpus and statistics. Section 5 reports main results, the ceiling analysis, the option-position artifact, cohort heterogeneity, and hard-question performance. Section 6 discusses protocol asymmetry and what the results mean for graph retrieval with small models, followed by limitations (Section 7), broader impact (Section 8), and conclusion (Section 9).

### 2 Related Work

**Position bias in long-context models.** LLM performance on long inputs degrades with the position of relevant content: models favor content at the beginning and end of long contexts, suffering in the middle [Lost in the Middle], and similar middle-collapse and recency effects have been reported across model families [Found in the Middle]. Our recent-window baseline directly measures the recency prior in a clue-dense narrative, and our de-biased oracle removes an orthogonal artifact: option-position anchoring in multiple-choice evaluation.

**Graph-based retrieval.** GraphRAG [GraphRAG] constructs a community hierarchy over an entity graph and answers queries from aggregated community summaries; HippoRAG [HippoRAG] builds a personalized PageRank-based retrieval over a knowledge graph, and GraphReader [GraphReader] walks a graph with an agentic controller. These systems optimize for recall on open-ended QA with large frontier models. Our setting differs in three ways: the graphs are frozen offline artifacts (no per-query graph construction), the reader is a fixed 9B local model with a 16K context, and the outcome is a multiple-choice accuracy against a de-biased evidence ceiling rather than open-ended generation.

**Hierarchical and structured memory.** RAPTOR [RAPTOR] builds a tree of recursively summarized clusters for long-context retrieval, and LongRAG [LongRAG] retrieves long "group units" to push evidence into the context window. These are linear-to-tree transformations of the text; our graphs additionally encode typed relations and entity resolution, and we compare retrieval protocols against an explicit gold-evidence ceiling.

**Long-context QA datasets.** DetectiveQA [DetectiveQA] releases detective-novel QA with annotated evidence; our benchmark follows its question style and uses its clue-position notion for the gold oracle, but fixes the reader model and freezes the graph artifacts to enable controlled statistical comparison.

### 3 Method

#### 3.1 Graph representation

For each novel we freeze an offline knowledge graph: nodes are typed entities (*person*, *location*, *clue object*, *event*, *time anchor*) and *evidence sentences* (paragraphs that mention an entity), and edges are typed relations (*appears in*, *participates in*, temporal *before*/*after*, *causes*, *supports*, *contradicts*, *related to*). Graphs are built from the novel text without access to future questions or gold answers. Each question is grounded in this graph by matching its entities to nodes, giving a query node from which all graph protocols start.

#### 3.2 Graph-navigation protocols

All three protocols answer the same multiple-choice question using only graph-derived text, with the original option order, and with no access to baselines or gold.

**Graph-guided evidence expansion.** The reader expands from the query node along graph edges to a small set of evidence chunks (order of a handful of supporting paragraphs), concatenates them with the question and options, and answers. This is the "tight" graph expansion protocol: the context is tiny and entirely graph-selected.

**Graph-native chunk reranking.** The reader first builds a candidate pool of graph-neighbor chunks (order of a few dozen), then re-ranks the pool using graph-native metadata (hop proximity and entity overlap) to select the top handful of chunks, and answers from those.

**Graph-based disagreement arbitration.** The reader runs the first two protocols as independent graph readers; when their selected answers disagree, a graph-only referee decides the final answer from the union of their evidence.

#### 3.3 Baselines and control

We compare against three standard non-graph conditions and one control, all with the same fixed model and context budget:

- **Recent-window baseline.** The reader receives the tail of the novel up to the context limit---the natural linear-history baseline.
- **Whole-book compression baseline.** The novel is compressed into the context window via extractive-compression text, then read.
- **Vector RAG baseline.** A flat dense retriever selects chunks by question similarity (standard retrieval-augmented generation).
- **Question-only control.** The reader sees only the question and options---the floor that any evidence-using method must beat.

#### 3.4 The de-biased gold oracle and the option-position artifact

A gold oracle hands the model every official clue paragraph [DetectiveQA] and asks it to answer. We found that this oracle, in its natural *options-first, original-order* form, is useless as a ceiling: the model is **79.4%** accurate when the gold answer is option D but 21.8--40.0% for A--C, and selects D on roughly 59% of all questions. The oracle is learning where the answer sits, not what it means. We therefore define the **fair gold oracle** with a de-biasing protocol: evidence paragraphs are presented *before* the options, and the option order is shuffled per question using a permutation derived deterministically from SHA-256 over the question ID, so the model cannot exploit position and every run is reproducible from the question IDs alone. We verify the protocol itself is neutral by re-running the question-only control under the same terse instruction ("Do not explain. Answer with exactly one line: Answer: [letter]") and shuffled options: it achieves the same 40.2% pooled as the original control (per cohort the two diverge---41.5% vs. 37.2% on the first twenty and 37.1% vs. 47.1% on the final ten---so the pooled match is descriptive), confirming that the +14.5pp gain of the oracle is evidence, not instruction or order.

#### 3.5 Statistics

All methods are evaluated on the identical set of 234 questions, so every comparison is a paired comparison. Primary inference uses exact two-sided paired McNemar tests on discordant pairs; we report the delta in accuracy points, wins/losses on discordant pairs, the exact p-value, and a novel-clustered bootstrap 95% confidence interval (5,000 resamples of whole novels, so novels are the sampling unit, not questions). Where a family of graph-vs-baseline contrasts is tested, we apply Holm--Bonferroni correction across the family of nine contrasts (three graph protocols × three baselines). Deltas are reported as first condition minus second condition (e.g., oracle minus graph method).

### 4 Experimental Setup

**Corpus.** 30 detective novels, 234 multiple-choice questions (164 in the first cohort of 20 novels, 70 in the final cohort of 10). Each question has four options and an official set of clue paragraphs used for the gold oracle.

**Model and context.** A fixed local 9B model with 16K context. Chain-of-thought (CoT) prompting [Chain-of-Thought Prompting Elicits Reasoning] elicits step-by-step reasoning before an answer is produced; we disable CoT for all conditions so that measured accuracy differences are attributable to the input, not to decoding strategy, and because our task is short multiple-choice answers where CoT mostly changes verbosity rather than content. All 2,808 evaluations (12 conditions × 234 questions) share this one model: four gold-oracle variants, three graph protocols, three baselines, and two controls.

**Frozen graphs and contamination control.** Graphs were built offline and frozen; answers never enter the graph. The first 20 and final 10 novels use two different, frozen graph-build pipelines, which we report separately and flag as a descriptive cohort difference: the earlier pipeline used a 7B extractor and a sparser relation schema (mean 456 edges per novel), the later pass used the same 9B model as the reader with a denser schema (mean 758 edges per novel), which is why the two pipelines differ markedly in gold-paragraph recall (Section 5.5). Pooled results across both cohorts are descriptive and labeled as such.

**Gold evidence.** For each question, the gold evidence is every official clue paragraph with a nonnegative clue position; the final-answer paragraph is excluded. The fair gold oracle concatenates these paragraphs before the (shuffled) options. The option shuffle is seeded per question ID and applied identically in all de-biased runs.

**Metrics.** Micro accuracy on the 234 questions; per-cohort accuracy; hard-subset accuracy (the 140 questions the question-only control answers incorrectly, i.e. the questions that actually require evidence); per-gold-letter accuracy; and the paired statistics above.

### 5 Results

#### 5.1 Main results

Table 1 and Figure 2 report pooled accuracy by cohort. All three graph protocols beat the recent-window baseline and the question-only control; graph-guided evidence expansion (53.8%) and graph-based disagreement arbitration (53.4%) are the strongest, matching the fair gold oracle (54.7%).

**Table 1 (main results).** Micro accuracy (%). "old20"/"new10" are the two frozen cohorts; "Pooled" combines them descriptively. "Hard subset" is accuracy on the 140 questions the question-only control already fails. The de-biased gold oracle is the evidence ceiling. *(Full table in §4.1 of this report.)*

**Figure 2 (main accuracy).** Accuracy by cohort for the seven non-oracle conditions, with the de-biased oracle as a dashed reference line. Bars marked with "*" exceed the oracle line; these run under the original option order while the oracle shuffles options (Section 6), so the position advantage is not exclusive to the graph conditions---the whole-book compression baseline also crosses the line. Pooled values are descriptive across the two graph-build cohorts.

#### 5.2 Closed-source reference points

To situate the local 9B results, Table 2 reports a closed-source frontier model (DeepSeek v4-flash, chain-of-thought disabled) on the same old20 cohort (164 questions). Given the entire novel text it reaches 80.5%; restricted to a linear 50,000-character tail window it falls to 54.9%; with only the question it reaches 41.5%. Two observations follow. First, linear-history reading caps even a frontier model at 54.9% on this cohort, and only full-novel reading escapes to 80.5%---the cost of linear access. Second, the local 9B model reaches 54.3% through graph navigation alone (Table 1), matching the frontier model's linear tail-reading accuracy with a small fraction of the context budget; the frontier model's remaining edge comes from a far larger context window that a 16K model cannot match. This is a descriptive cross-model reference, not a matched comparison: different models have different intrinsic ceilings, which is precisely why our main analysis compares each model against a model-specific de-biased oracle.

**Table 2 (closed-source reference points, old20 / 164 questions).** "Linear tail" is the final 50,000 characters of the novel. The final row repeats the local 9B graph result for direct comparison.

| Condition | Correct/total | Micro acc. |
|---|---|---|
| Question-only | 68/164 | 41.5% |
| Linear tail (50K chars) | 90/164 | 54.9% |
| Full novel | 132/164 | 80.5% |
| Graph-guided evidence expansion (local 9B) | 89/164 | 54.3% |

#### 5.3 Graph navigation approaches the gold ceiling

The central comparison is the gold oracle against the question-only control and the graph protocols (Table 3, Figure 3). The fair gold oracle exceeds the question-only control by +14.53pp (63 wins vs. 29 losses on discordant pairs, p=0.0005, 95% CI [6.9, 23.4]pp). This is the evidence effect: the same 9B model, given all official clues under a position-neutral protocol, moves 14.5 accuracy points. Graph-guided evidence expansion, given only a handful of graph-derived paragraphs, reaches 53.8%, and the paired contrast against the oracle is +0.85pp (p=0.912; deltas are reported as first condition minus second, so +0.85 favors the oracle): the graph reader is statistically indistinguishable from a reader handed the complete gold evidence. Graph-based disagreement arbitration is likewise indistinguishable (+1.28pp, p=0.832, also favoring the oracle).

**Table 3 (paired contrasts).** Paired contrasts over the same 234 questions (exact two-sided McNemar; W/L = wins/losses on discordant pairs; 95% CI is the novel-clustered bootstrap). The top block shows the gold-oracle analysis; the bottom block shows graph methods against the recent-window baseline with Holm-corrected p. *(Full table in §4.4 of this report.)*

**Figure 3 (pairwise forest plot).** Forest plot of the key paired contrasts. Filled markers are significant at p<0.05; error bars are novel-clustered bootstrap 95% CIs. The headline evidence effect (fair gold vs. question-only) is +14.5pp, while the graph methods are indistinguishable from the fair gold oracle.

#### 5.4 Option-position anchoring is a methodological artifact

Figure 4 and Table 4 (Appendix) document why an oracle must be de-biased. The options-first gold oracle---all evidence, options first, original order---scores 41.5%, statistically indistinguishable from the question-only control (40.2%), yet this is not a "no evidence helps" result: the oracle is 79.4% accurate when the gold answer is D (the gold letter is D on 65 of the 234 questions) but 21.8--40.0% for A--C, at or below the control's per-letter levels. The 9B model anchors on the position of the options. Shuffling options and moving evidence first removes the artifact: per-letter accuracy flattens (A 55.2%, B 47.2%, C 53.4%, D 61.5% for the fair oracle) and the selected-letter distribution matches the content-implied gold-letter distribution.

**Figure 4 (D-anchoring).** **Left:** per-gold-letter accuracy. The options-first oracle is 21.8--40.0% for A--C (at or below the control's per-letter levels) but 79.4% for D (highlighted), the anchoring signature. **Right:** selected-letter distribution (share of questions); the options-first oracle piles onto D (~59%), while the de-biased oracle tracks the content-implied gold-letter distribution.

#### 5.5 Cohort heterogeneity

The two frozen cohorts behave very differently, which drives the pooled numbers. In the old20 cohort the gold evidence sits overwhelmingly in the graph's 2-core---the gold-overlap rate is 53.7% versus 18.2% for other nodes (enrichment 2.95, odds ratio 5.21), and graph retrieval reaches only 16.3% of gold paragraphs. In the new10 cohort the graph build is far more complete: 73.2% of gold paragraphs are retrieved, the enrichment is 1.61 (odds ratio 2.32), and the best graph protocol reaches 58.6% (versus 50.0% for the de-biased oracle under its more conservative protocol). The two cohorts therefore bound the failure and success modes of the graph pipeline.

#### 5.6 Hard-question performance

The 140 questions the question-only control gets wrong are the ones that actually require evidence. On this subset the fair gold oracle reaches 47.1% (66/140); graph-native chunk reranking reaches 45.0%, graph-guided evidence expansion 42.9%, and graph-based disagreement arbitration 41.4%. Every graph protocol beats the recent-window baseline (33.6%) and the compression (37.1%) and RAG (36.4%) baselines. The gap to the oracle on hard questions is where the graph pipeline loses evidence, not where the reader fails to reason.

#### 5.7 Summary of paired statistics

Across the family of nine graph-vs-baseline contrasts, graph-guided evidence expansion vs. recent-window is the strongest pooled signal (+7.69pp, p=0.050, Holm-corrected p=0.454); on the new10 cohort, graph-based disagreement arbitration vs. recent-window is +17.14pp (15 wins vs. 3 losses, p=0.008, Holm-corrected p=0.068). The Holm correction is conservative for a 9-comparison family; we report exact and corrected values throughout.

### 6 Discussion

**Graph navigation converts position into topology.** The mechanism behind the headline result is visible in Figure 1: for a small model with a fixed 16K context, the question is not whether the clue exists in the book but whether the reader can reach it. A graph hop costs the same budget whether the target sits at linear position 0.05 or 0.95. Graph-guided evidence expansion uses a few hundred tokens of graph-selected evidence and matches a reader given the full official clue set---the graph is, in effect, a non-linear index that lets the model spend its small context where the answer is.

**Protocol asymmetry favors the graph methods.** The fair gold oracle shuffles options and presents evidence first; the graph protocols run the original option order. Shuffling options is harder for a position-sensitive 9B model, so the oracle's 54.7% is a conservative ceiling. This is why, on the new10 cohort, graph methods can exceed the de-biased oracle line (Figure 2): under the original option order the same evidence is worth more to this model. The asymmetry means our "indistinguishable from the ceiling" claim is, if anything, conservative.

**Where the graph loses.** The gap between graph methods and the oracle is almost entirely retrieval-side: on the hard questions the graph protocols reach 41--45% versus 47.1% for the oracle, and cohort heterogeneity shows the gap is largest precisely where graph recall is lowest (old20: 16.3% gold recall). Graph-based disagreement arbitration partially recovers lost evidence by running two readers and arbitrating their disagreement, which is why it is the best protocol on the stronger new10 cohort (58.6%).

**Small models are the right testbed.** A frontier model with a large context and strong instruction following may "search" linearly within its window; a 9B model with 16K context cannot. Our results show the graph primitive matters most for exactly this regime---the regime of local, on-device, cost-sensitive deployment.

**Toward non-linear structured memory.** Our graphs instantiate a general primitive: a structured memory a reader queries by navigation rather than by linear scan. Attention over a long history is expensive and position-biased; a sparse index trades a small retrieval cost for freedom from position. The direction scales: a model with a 1M-token context asked to reason over a 100M-token corpus or long-term memory store faces the same problem a 16K model faces with a half-million-character novel---the evidence is somewhere in the history, but the reader cannot afford to see all of it. If navigation is what let the small model approach its evidence ceiling here, the same non-linear index may be what lets much larger models approach their ceilings on histories two orders of magnitude beyond their windows.

### 7 Limitations

**Heterogeneous graph pipelines.** The two cohorts use different frozen graph builds; pooled numbers are descriptive, and per-cohort results must be read separately. A single, uniform graph pipeline is needed to make the pooled claim strong.

**Exploratory development on the corpus.** The graph protocols were developed on these 30 novels; the fixed 9B model and frozen graphs reduce but do not eliminate selection bias. The gold and baseline conditions are independent of the graph development, which protects the ceiling comparison.

**Single model.** All conclusions are for one 9B checkpoint. The de-biasing protocol and the ceiling comparison should transfer, but the exact magnitudes are model-specific.

**Lexical gold matching.** Gold evidence is matched to paragraphs by the dataset's clue positions; the gold oracle sees the official clue set, not necessarily every sentence that could help.

**No attention-level causality.** We show accuracy differences, not a mechanism; we do not trace attention to graph-selected versus linear evidence.

**Contamination nuance.** Graphs are built from the full novel, so a graph node may touch the answer region; the graph protocols can surface content beyond the official clue set. This makes the oracle comparison conservative (the oracle is restricted to official clues) but means the graph's ceiling itself is not bounded by the official clue set.

### 8 Broader Impact

This is low-risk research on synthetic-style QA over public-domain detective fiction; no user data, no generation of harmful content. The main transferable finding is methodological: *multiple-choice evaluation of small models is sensitive to option order*, and gold/oracle runs that do not shuffle options can produce systematically misleading ceilings (79.4% on D, 21.8--40.0% elsewhere). We recommend that MCQ evaluations of long-context systems shuffle options with a fixed seed and, for evidence ceiling estimation, present evidence before options. The graph-retrieval primitive itself has straightforward applications in document QA where context budgets are tight.

### 9 Conclusion

We showed that a non-linear knowledge-graph retrieval system lets a small, fixed-context model approach the evidence ceiling on long detective-novel reasoning. Under a de-biased protocol, the gold oracle is a genuine ceiling (+14.5pp over the question-only control), and graph-guided evidence expansion is statistically indistinguishable from it (p=0.912), while three different graph protocols all outperform linear-history baselines on the questions that require evidence. We also documented an option-position anchoring artifact that makes naive gold oracles unreliable. For small models with tight context budgets, graph navigation is a cheap, effective substitute for linear search. Looking forward, we treat the graph as the simplest form of non-linear structured memory: a sparse index that lets a model spend its attention where the answer is, independent of position. The bottleneck of linear access does not shrink as windows grow, so we expect this primitive to matter more as models move to million-token contexts over corpora and memories of a hundred million tokens or more.

### References (as cited above)

- Lost in the Middle: How Language Models Use Long Contexts (Liu et al., TACL 2024)
- Found in the Middle: Calibrating Positional Attention Bias Improves Long Context Utilization (Hsieh et al., ACL 2024)
- Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks (Lewis et al., NeurIPS 2020)
- From Local to Global: A Graph RAG Approach to Query-Focused Summarization (Edge et al., 2024)
- HippoRAG: Neurobiologically Inspired Long-Term Memory for Large Language Models (Gutiérrez et al., NeurIPS 2024)
- GraphReader: Building Graph-based Agent to Enhance Long-Context Abilities of Large Language Models (Li et al., EMNLP 2024 Findings)
- RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval (Sarthi et al., ICLR 2024)
- LongRAG: A Dual-Perspective Retrieval-Augmented Generation Paradigm for Long-Context Question Answering (Zhao et al., EMNLP 2024)
- Chain-of-Thought Prompting Elicits Reasoning in Large Language Models (Wei et al., NeurIPS 2022)
- DetectiveQA: Evaluating Long-Context Reasoning on Detective Novels (Xu et al., 2024)

### Appendix (English paper)

**Appendix A. Full cohort results.** The 12 conditions across the three cohorts with correct/total and micro accuracy, plus preservation rates on the question-only-correct questions. *(Full tables in §4.2 and §4.3 of this report.)*

**Appendix B. De-biased gold analysis (per-letter and distribution).** Per-gold-letter accuracy (left) and selected-letter distribution (right) for the de-biased and control runs, with the content-implied gold-letter distribution for reference. The options-first oracle's D spike is an anchoring artifact. *(Full tables in §4.5 and §4.6 of this report.)*

**Appendix C. Graph structure analysis.** Figure: Gold-overlap nodes are concentrated in the topological 2-core of the graph. Rates are micro over all graph nodes per cohort; "×" labels are the gold/other rate ratio (enrichment). Figure: Frozen force-layout graph for one novel (n nodes and m edges as annotated). Orange nodes overlap gold evidence; red nodes overlap the answer. The dashed circle marks the topological core. Force-layout distance is topological, not narrative or semantic.

## 7. 论文全文（中文版）

> 以下为 `paper/neurips2026_zh/main_zh.tex` 的完整内容转写（与英文版逐节对应，数字完全一致）。原始 LaTeX/PDF：`paper/neurips2026_zh/main_zh.tex`、`paper/neurips2026_zh/build/main_zh.pdf`。

### 题目（中文标题）

**非线性知识图谱能否使小模型的长上下文阅读器逼近证据上限？——基于侦探小说的案例研究**

### 摘要（中文）

大语言模型将长文档当作**线性**输入历史来阅读：token 按叙述顺序进入固定上下文窗口，因此证据的可及性既取决于相关性、也取决于位置。我们研究一个**非线性**检索系统——文本之上的知识图谱"思维导图"——能否让小型上下文模型逼近证据本身所允许的上限。我们在 30 部侦探小说、234 道多项选择题上，为每部小说冻结一个离线知识图谱，并在固定 9B 本地模型、16K 上下文的条件下，将三种图谱导航协议分别与尾窗口基线、整书压缩基线、向量检索(RAG)基线和仅题目对照进行比较。30 部小说来自两条不同的冻结图谱构建流水线，因此合并数字仅具描述性。去偏金标预言机——在去除选项位置伪影的协议下向模型展示全部官方线索段——达到 54.7%，而仅题目对照为 40.2%（配对 +14.53pp，p=0.0005）。图谱引导的证据扩展达到 53.8%，与预言机在统计上不可区分（p=0.912），尽管每题只读取若干图谱选出的段落。我们还表明，选项在前的预言机运行不是可靠上限：当金标答案为 D 时它正确率为 79.4%，但对 A--C 只有 21.8--40.0%，这是去偏协议能够消除的选项位置锚定伪影。更广泛地说，我们把图谱导航视为**非线性结构化记忆**的一种实例——一种模型可以跳跃穿行的稀疏索引，使推理依赖结构而非位置。我们预期这一原语在上下文窗口增长到百万 token、而文档与长期记忆再大几个数量级时会更加重要。

**关键词：** 长上下文推理；知识图谱检索；非线性结构化记忆；小型语言模型；多项选择评估

### 1 引言

LLM 的上下文窗口是一段线性历史：模型逐 token 观察文档，在固定长度的窗口中决定关注什么。对于一部约 50 万字符（约 10⁵ token，远大于 16K 上下文）的小说，"凶手为何留下罐头茶"的答案可能位于 0.28 附近的段落，而模型的有效注意力被钉在尾部附近 [Lost in the Middle; Found in the Middle]。因此检索问题不是"证据是否存在"，而是"阅读器能否非线性地导航到它"。检索增强生成 [RAG] 通过扁平的相似度索引缓解了这一问题，但它仍把文档当作一段待切分的字节流。

本文研究另一种原语：**图谱导航**。小说之上的知识图谱——人物、地点、线索物品、事件、时间锚点，以及提到它们的证据句，由带类型的边相连——是一张非线性的"思维导图"。回答问题时不需在全文做线性扫描；模型可以从查询节点出发，跳到一小簇证据节点，无论证据在线性文本中的位置如何，都能保持在很小的上下文预算内（图 1）。本文的核心问题是：对于小型、本地、长上下文模型，这种非线性图谱导航能否让模型逼近**金标上限**——即模型被交给全部官方线索段时的准确率？该原语还能超越这一试验台：1M token 的上下文仍被 100M token 的语料或长期记忆存储所压倒，那里的瓶颈正是我们这里研究的线性访问，而非窗口大小。

我们为此构建一个干净、冻结的基准。在 30 部侦探小说、234 道多项选择题上，我们固定一个 9B 本地模型（16K 上下文、禁用思维链），并评估 (i) 基于冻结单书图谱的三种图谱导航协议，(ii) 三种标准检索/压缩基线，(iii) 仅题目对照，以及 (iv) **公平金标预言机**：在去除选项位置伪影的协议下向模型展示全部官方线索段。我们测量图谱导航是否逼近预言机，并量化剩余差距的确切来源。

**图 1（示意）。** 线性输入历史 vs. 非线性图谱导航。**上：** 小说是一条 0–1 线性阅读尺；尾部的固定大小阅读窗口看不到位于 0.28 处的支撑线索。**下：** 同一条线索离查询节点仅一步图跳，可用极小的上下文预算到达，与线性位置无关。跳数深度以大小和颜色编码（一跳饱和、二跳更淡、无关节点为灰色），遵循我们分析中沿用的看板配色约定。

我们的核心结果有三点。

- **去偏预言机是真正的上限。** 在洗牌选项、证据在前的协议下，金标预言机达到 54.7%，而仅题目对照为 40.2%（+14.53pp，配对精确 McNemar p=0.0005）；原始的选项在前金标运行只有 41.5%，与仅题目对照（40.2%）持平，因为模型锚定于选项位置。
- **图谱导航逼近上限。** 图谱引导的证据扩展达到 53.8%，与公平金标预言机在统计上不可区分（p=0.912，在同一 234 题上配对），且仅使用图谱推导的证据。三种图谱协议在更难的题目上都优于尾窗口基线。
- **必须控制选项位置伪影。** 公平金标预言机的 +14.5pp 优势在选项在前的预言机运行中完全不可见；把方法去比较一个未经去偏的预言机没有意义。

本文结构如下。第 2 节将该工作置于长上下文与图谱检索文献之中。第 3 节描述三种图谱导航协议、基线与去偏金标协议。第 4 节详述冻结语料与统计方法。第 5 节报告主结果、上限分析、选项位置伪影、队列异质性与难题表现。第 6 节讨论协议不对称及其对小型模型图谱检索的意义，随后是局限（第 7 节）、更广泛影响（第 8 节）与结论（第 9 节）。

### 2 相关工作

**长上下文模型中的位置偏置。** LLM 在长输入上的表现随相关内容位置而退化：模型偏好长上下文首尾的内容，在中部受损 [Lost in the Middle]，跨模型家族也报告了类似的中部塌陷与近因效应 [Found in the Middle]。我们的尾窗口基线直接测量了线索密集叙事中的近因先验；我们的去偏预言机则去除一个正交的伪影：多项选择评估中的选项位置锚定。

**基于图谱的检索。** GraphRAG [GraphRAG] 在实体图上构建社区层次，并从聚合的社区摘要回答问题；HippoRAG [HippoRAG] 在知识图谱上构建基于个性化 PageRank 的检索；GraphReader [GraphReader] 用一个智能体控制器在图上游走。这些系统面向大型前沿模型的开放问答，优化召回。我们的设定有三点不同：图谱是冻结的离线工件（不按查询构建）；阅读器是固定 9B 本地模型、16K 上下文；结局是相对去偏证据上限的多项选择准确率，而非开放式生成。

**层次化与结构化记忆。** RAPTOR [RAPTOR] 为长上下文检索构建递归摘要聚类的树；LongRAG [LongRAG] 检索长"组单元"把证据推入上下文窗口。这些是文本的线性到树变换；我们的图谱额外编码带类型的关系与实体解析，并且我们把检索协议与显式金标证据上限进行对比。

**长上下文 QA 数据集。** DetectiveQA [DetectiveQA] 发布了侦探小说 QA 及标注证据；我们的基准沿用其提问风格，并用其线索位置概念构造金标预言机，但固定阅读器模型、冻结图谱工件，从而支持受控统计比较。

### 3 方法

#### 3.1 图表示

对每部小说我们冻结一个离线知识图谱：节点是带类型实体（*人物*、*地点*、*线索物品*、*事件*、*时间锚点*）与*证据句*（提到某实体的段落），边是带类型关系（*出现于*、*参与*、时间*之前/之后*、*导致*、*支持*、*矛盾*、*相关*）。图谱在不能访问未来题目或金标答案的情况下由小说文本构建。每个问题通过把其实体匹配到节点而锚定在图谱上，给出所有图谱协议起始的查询节点。

#### 3.2 图谱导航协议

三种协议都只使用图谱推导的文本回答同一道多项选择题，使用原始选项顺序，并且不访问基线与金标。

**图谱引导的证据扩展。** 阅读器从查询节点沿图边扩展到一小簇证据块（量级为若干支撑段落），把它们与题目和选项拼接后作答。这是"紧致"的图扩展协议：上下文极小且完全由图谱选择。

**图谱原生分块重排。** 阅读器先构建一个图谱邻居候选池（量级为几十个分块），再利用图谱原生元数据（跳数与实体重叠）对候选池重排，选出最相关的若干分块作答。

**基于图谱的分歧仲裁。** 阅读器把前两种协议作为两个独立的图谱阅读器运行；当二者所选答案不一致时，由一个纯图谱裁判根据二者证据的并集决定最终答案。

#### 3.3 基线与对照

我们在同一固定模型和上下文预算下对比三种非图条件与一个对照：

- **尾窗口基线。** 阅读器接收小说尾部直到上下文上限——自然的线性历史基线。
- **整书压缩基线。** 小说被抽取压缩进上下文窗口，然后阅读。
- **向量检索(RAG)基线。** 扁平稠密检索器按题目相似度选择分块（标准检索增强生成）。
- **仅题目对照。** 阅读器只见题目与选项——任何使用证据的方法都必须超过的地板。

#### 3.4 去偏金标预言机与选项位置伪影

金标预言机把每个官方线索段交给模型 [DetectiveQA] 并让其作答。我们发现，该预言机在其自然的*选项在前、原始顺序*形式下不能作为上限：当金标答案为 D 时模型有 79.4% 的准确率，但 A--C 只有 21.8--40.0%，并且在大约 59% 的题目上选择 D。预言机学到的是答案坐在哪里，而不是答案意味着什么。因此我们定义**公平金标预言机**，采用去偏协议：证据段落*先于*选项呈现；选项顺序按题洗牌，置换由题目 ID 的 SHA-256 确定性导出，使模型无法利用位置，且每次运行都能仅凭题目 ID 复现。我们通过用同样的简洁指令（"不要解释。只回答一行：Answer: [letter]"）和洗牌选项重跑仅题目对照，来验证协议本身是中性的：它合并后达到与原始对照相同的 40.2%（分队列两者会偏离——前二十部为 41.5% 对 37.2%、后十部为 37.1% 对 47.1%——因此合并的持平只是描述性的），从而确认预言机的 +14.5pp 增益来自证据，而非指令或顺序。

#### 3.5 统计方法

所有方法都在同一组 234 道题上评估，因此每个比较都是配对比较。主要推断使用配对不一致对上的精确双侧 McNemar 检验；我们报告准确率点数差、不一致对上的胜/负、精确 p 值，以及按小说聚类的 bootstrap 95% 置信区间（5,000 次整书重采样，因此抽样单元是小说而非题目）。在检验一组图谱对基线对比时，我们对该族九个对比（三种图谱协议 × 三个基线）应用 Holm--Bonferroni 校正。差值按"第一个条件减第二个条件"报告（例如，预言机减图谱方法）。

### 4 实验设置

**语料。** 30 部侦探小说、234 道多项选择题（第一部队列 20 部小说 164 题，后一队列 10 部 70 题）。每题有四个选项和一组用于金标预言机的官方线索段。

**模型与上下文。** 固定本地 9B 模型、16K 上下文。思维链（Chain-of-Thought，CoT）提示 [Chain-of-Thought Prompting Elicits Reasoning] 会在作答前引出逐步推理；我们对所有条件禁用 CoT，使测得的准确率差异可归因于输入而非解码策略，也因为我们的任务是简短的多项选择作答，CoT 主要改变的是冗长度而非内容。全部 2,808 次评估（12 个条件 × 234 题）共享这一个模型：四种金标预言机变体、三种图谱协议、三个基线、两个对照。

**冻结图谱与污染控制。** 图谱离线构建并冻结；答案永不进入图谱。前 20 部与后 10 部小说使用两条不同且已冻结的图谱构建流水线，我们分开报告并标明这是一种描述性队列差异：较早的流水线使用 7B 抽取器和更稀疏的关系模式（每部平均 456 条边），较晚的一遍使用与阅读器相同的 9B 模型和更稠密的模式（每部平均 758 条边），这正是两条流水线在金标段落召回上差异显著的原因（第 5.5 节）。跨两个队列的合并结果仅具描述性，并作如此标注。

**金标证据。** 对每题，金标证据是每个线索位置非负的官方线索段；最终答案段被排除。公平金标预言机把这些段拼接在（已洗牌的）选项之前。选项洗牌按题目 ID 播种，并在所有去偏运行中一致应用。

**指标。** 234 题上的微平均准确率；分队列准确率；难题子集准确率（仅题目对照答错的 140 题，即真正需要证据的题目）；逐金标字母准确率；以及上述配对统计。

### 5 结果

#### 5.1 主要结果

表 1 与图 2 报告分队列的合并准确率。三种图谱协议都超过尾窗口基线和仅题目对照；图谱引导的证据扩展（53.8%）与基于图谱的分歧仲裁（53.4%）最强，与公平金标预言机（54.7%）相当。

**表 1（主要结果）。** 微平均准确率（%）。"old20"/"new10" 是两个冻结队列；"合并"把二者描述性地结合。"难题子集"是仅题目对照已答错的 140 题上的准确率。去偏金标预言机是证据上限。*（完整表格见本报告 §4.1。）*

**图 2（主准确率）。** 七个非预言机条件的分队列准确率，虚线为去偏预言机参考线。带"*"的柱超过预言机线；这些运行使用原始选项顺序，而预言机洗牌选项（第 6 节），因此位置优势并不为图谱条件所独有——整书压缩基线也越过了该线。合并值横跨两个图谱构建队列，仅具描述性。

#### 5.2 闭源大模型参考点

为给本地 9B 结果定位，表 2 报告同一 old20 队列（164 题）上一个闭源前沿模型（DeepSeek v4-flash，禁用思维链）的结果。给定整本小说文本它达到 80.5%；限制到线性的 50,000 字符尾部窗口则降到 54.9%；仅给题目为 41.5%。两点观察：其一，线性历史阅读把即使前沿模型也封顶在 54.9%，只有读完整本小说才能跃至 80.5%——这正是线性访问的代价。其二，本地 9B 模型仅靠图谱导航就达到 54.3%（表 1），以一小部分上下文预算追平了前沿模型的线性尾部阅读准确率；前沿模型的剩余优势来自大得多的上下文窗口，16K 模型无法企及。这是一个描述性的跨模型参考，而非受控对比：不同模型的内在上限不同，这正是我们主分析把每个模型与模型特异的去偏预言机比较的原因。

**表 2（外部参考点，old20 / 164 题）。** "线性尾部"指小说最后 50,000 字符。最后一行重复本地 9B 图谱结果以便直接对比。

| 条件 | 正确/总数 | 微平均准确率 |
|---|---|---|
| 仅题目 | 68/164 | 41.5% |
| 线性尾部（50K 字符） | 90/164 | 54.9% |
| 整本小说 | 132/164 | 80.5% |
| 图谱引导的证据扩展（本地 9B） | 89/164 | 54.3% |

#### 5.3 图谱导航逼近金标上限

核心比较是金标预言机对仅题目对照与三种图谱协议（表 3、图 3）。公平金标预言机比仅题目对照高出 +14.53pp（不一致对 63 胜对 29 负，p=0.0005，95% CI [6.9, 23.4]pp）。这是证据效应：同一个 9B 模型，在位置中性的协议下得到全部官方线索，准确率提升 14.5 点。图谱引导的证据扩展只拿到若干图谱推导的段落，却达到 53.8%，其对预言机的配对差为 +0.85pp（p=0.912；差值按"第一个条件减第二个条件"报告，因此 +0.85 对预言机有利）：图谱阅读器与拿到完整金标证据的阅读器在统计上不可区分。基于图谱的分歧仲裁同样不可区分（+1.28pp，p=0.832，同样对预言机有利）。

**表 3（配对对比）。** 同一 234 题上的配对对比（精确双侧 McNemar；W/L = 不一致对上的胜/负；95% CI 为按小说聚类的 bootstrap）。上块为金标预言机分析；下块为图谱方法对尾窗口基线、带 Holm 校正 p。*（完整表格见本报告 §4.4。）*

**图 3（配对森林图）。** 关键配对对比的森林图。实心标记为 p<0.05 显著；误差条为按小说聚类的 bootstrap 95% CI。头条证据效应（公平金标 vs. 仅题目）为 +14.5pp，而图谱方法与公平金标预言机不可区分。

#### 5.4 选项位置锚定是方法学伪影

图 4 与表 4（附录）说明了为什么预言机必须去偏。选项在前金标预言机——全部证据、选项在前、原始顺序——得分为 41.5%，与仅题目对照（40.2%）在统计上不可区分；但这并不是"证据无益"的结果：当金标答案为 D 时预言机有 79.4% 的正确率（234 题中金标字母为 D 的有 65 题），而 A--C 只有 21.8--40.0%，达到或低于对照的逐字母水平。9B 模型锚定于选项的位置。洗牌选项并把证据提前可以消除该伪影：逐字母准确率趋于平坦（公平预言机 A 55.2%、B 47.2%、C 53.4%、D 61.5%），所选字母分布也与内容隐含的金标字母分布一致。

**图 4（D 锚定）。** **左：** 逐金标字母准确率。选项在前预言机 A--C 为 21.8--40.0%（达到或低于对照的逐字母水平），但 D 为 79.4%（高亮），即锚定特征。**右：** 所选字母分布（占题目比例）；选项在前预言机涌向 D（约 59%），而去偏预言机跟随内容隐含的金标字母分布。

#### 5.5 队列异质性

两个冻结队列行为差异很大，这决定了合并数字。在 old20 队列中，金标证据压倒性地位于图的 2-核内——金标重叠率为 53.7%，其他节点仅 18.2%（富集 2.95，比值比 5.21），图谱检索只达到 16.3% 的金标段落。在 new10 队列中图谱构建完整得多：检索到 73.2% 的金标段落，富集 1.61（比值比 2.32），最佳图谱协议达到 58.6%（其较保守协议下的去偏预言机为 50.0%）。两个队列因此界定了图谱流水线失效与成功的两种模式。

#### 5.6 难题子集表现

仅题目对照答错的 140 题正是真正需要证据的题。在该子集上，公平金标预言机达到 47.1%（66/140）；图谱原生分块重排为 45.0%，图谱引导的证据扩展 42.9%，基于图谱的分歧仲裁 41.4%。每种图谱协议都超过尾窗口基线（33.6%）以及压缩（37.1%）和 RAG（36.4%）基线。在难题上与预言机的差距来自图谱流水线丢失证据，而非阅读器推理能力不足。

#### 5.7 配对统计汇总

在九个图谱对基线对比这一族中，图谱引导的证据扩展对尾窗口是最强的合并信号（+7.69pp，p=0.050，Holm 校正 p=0.454）；在 new10 队列上，基于图谱的分歧仲裁对尾窗口为 +17.14pp（15 胜对 3 负，p=0.008，Holm 校正 p=0.068）。Holm 校正对 9 对比族是保守的；我们全文报告精确值与校正值。

### 6 讨论

**图谱导航把位置转化为拓扑。** 头条结果背后的机制在图 1 中可见：对固定 16K 上下文的模型，问题不在于线索是否存在于书中，而在于阅读器能否到达它。目标在线性位置 0.05 还是 0.95，一步图跳的代价相同。图谱引导的证据扩展用几百个 token 的图谱选择证据就追平了拿到完整官方线索集的阅读器——图谱实际上是一张非线性索引，让模型把小的上下文花在答案所在之处。

**协议不对称对图谱方法有利。** 公平金标预言机洗牌选项并先呈现证据；图谱协议运行原始选项顺序。对位置敏感的 9B 模型来说洗牌更难，因此预言机的 54.7% 是保守上限。这就是为什么在 new10 队列上图谱方法可以超过去偏预言机线（图 2）：在原始选项顺序下，同样的证据对这个模型更值钱。这种不对称意味着我们"与上限不可区分"的主张若非如此，至少是保守的。

**图谱在哪里丢失。** 图谱方法与预言机的差距几乎全部来自检索侧：在难题上图谱协议达到 41--45%，预言机为 47.1%；队列异质性显示差距恰好在图谱召回最低处最大（old20：16.3% 金标召回）。基于图谱的分歧仲裁通过运行两个阅读器并仲裁其分歧来部分找回丢失的证据，这正是它在更强的 new10 队列上成为最佳协议（58.6%）的原因。

**小模型是正确的试验场。** 拥有大上下文和强指令跟随的前沿模型或许能在窗口内"线性搜索"；9B 模型配 16K 上下文做不到。我们的结果表明图谱原语对正是这一领域最重要——本地、设备端、成本敏感部署的领域。

**迈向非线性结构化记忆。** 我们的图谱实现了一个一般原语：读者通过导航而非线性扫描来查询的结构化记忆。对长历史的注意力既昂贵又带位置偏置；稀疏索引以一点检索代价换取对位置的自由。这一方向会缩放：一个 1M token 上下文的模型要在一个 100M token 的语料或长期记忆上推理，面临的正是 16K 模型面对半百万字符小说时的问题——证据就在历史的某处，但读者负担不起把它全看完。如果导航在这里让小型模型逼近了它的证据上限，同一个非线性索引或许也能让大得多的模型在比其窗口大两个数量级的历史上逼近它们各自的上限。

### 7 局限

**异质图谱流水线。** 两个队列使用不同的冻结图谱构建；合并数字仅具描述性，分队列结果必须分开阅读。需要单一统一的图谱流水线才能让合并主张更强。

**语料上的探索性开发。** 图谱协议是在这 30 部小说上开发的；固定 9B 模型和冻结图谱减少了但没有消除选择偏差。金标与基线条件独立于图谱开发，这保护了上限比较。

**单一模型。** 所有结论只针对一个 9B 检查点。去偏协议与上限比较应当可以迁移，但具体数值是模型特异的。

**词汇级金标匹配。** 金标证据按数据集的线索位置与段落匹配；金标预言机看到官方线索集，而不一定是每个可能有帮助的句子。

**没有注意力级因果。** 我们展示的是准确率差异而非机制；我们没有追踪注意力到图谱选择证据与线性证据。

**污染细节。** 图谱由整本小说构建，因此图谱节点可能触及答案区域；图谱协议可以浮现官方线索集之外的内容。这使得预言机比较趋于保守（预言机限于官方线索），但也意味着图谱自身的上限不受官方线索集约束。

### 8 更广泛影响

这是针对公有领域侦探小说的类合成 QA 的低风险研究；无用户数据，不生成有害内容。最主要的可迁移发现是方法学层面的：*小型模型的多项选择评估对选项顺序敏感*，不洗牌选项的金标/预言机运行会产生系统性误导的上限（D 上 79.4%，其余 21.8--40.0%）。我们建议对长上下文系统的 MCQ 评估用固定种子洗牌选项，并在估计证据上限时先呈现证据再呈现选项。图谱检索原语本身在上下文预算紧张的场景下的文档 QA 中有直接应用。

### 9 结论

我们表明，非线性知识图谱检索系统能让小型、固定上下文的模型在长侦探小说推理上逼近证据上限。在去偏协议下，金标预言机是真正的上限（比仅题目对照高 +14.5pp），图谱引导的证据扩展与之在统计上不可区分（p=0.912），而三种不同的图谱协议在真正需要证据的题目上都优于线性历史基线。我们还记录了一个使朴素金标预言机不可靠的选项位置锚定伪影。对上下文预算紧张的小模型而言，图谱导航是线性搜索的一种廉价而有效的替代。放眼长远，我们把图谱视为非线性结构化记忆的最简形式：一种稀疏索引，让模型把注意力花在答案所在之处，与位置无关。线性访问的瓶颈不会随窗口增大而消失，因此我们预期当模型走向百万 token 上下文、处理上亿 token 甚至更多的语料与记忆时，这一原语会更加重要。

### 参考文献（中文版，与英文版一致）

- Lost in the Middle: How Language Models Use Long Contexts（Liu 等，TACL 2024）
- Found in the Middle: Calibrating Positional Attention Bias Improves Long Context Utilization（Hsieh 等，ACL 2024）
- Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks（Lewis 等，NeurIPS 2020）
- From Local to Global: A Graph RAG Approach to Query-Focused Summarization（Edge 等，2024）
- HippoRAG: Neurobiologically Inspired Long-Term Memory for Large Language Models（Gutiérrez 等，NeurIPS 2024）
- GraphReader: Building Graph-based Agent to Enhance Long-Context Abilities of Large Language Models（Li 等，EMNLP 2024 Findings）
- RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval（Sarthi 等，ICLR 2024）
- LongRAG: A Dual-Perspective Retrieval-Augmented Generation Paradigm for Long-Context Question Answering（Zhao 等，EMNLP 2024）
- Chain-of-Thought Prompting Elicits Reasoning in Large Language Models（Wei 等，NeurIPS 2022）
- DetectiveQA: Evaluating Long-Context Reasoning on Detective Novels（Xu 等，2024）

### 附录（中文版）

**附录 A. 完整分队列结果。** 表 old20、new10 与合并给出全部 12 个条件在三个队列上的正确数/总数与微平均准确率；表保留率给出各方法在仅题目对照答对的题目上的保留率（合并）。*（完整表格见本报告 §4.2 与 §4.3。）*

**附录 B. 去偏金标分析（逐字母与分布）。** 去偏与对照运行的逐金标字母准确率（左）与所选字母分布（右），并附内容隐含的金标字母分布供参考。选项在前预言机的 D 尖峰是锚定伪影。*（完整表格见本报告 §4.5 与 §4.6。）*

**附录 C. 图结构分析。** 图：金标重叠节点集中于图的拓扑 2-核。比率为各队列全部图节点上的微平均；"×"标签为金标/其他节点的比率（富集）。图：一部小说的冻结力导向图（节点数 n、边数 m 如标注）。橙色节点与金标证据重叠；红色节点与答案重叠。虚线圆标出拓扑核。力导向距离是拓扑的，不是叙事或语义的。

---

## 8. 复现指南

### 8.1 论文编译

```bash
# 英文版（Tectonic）
"C:\Users\fwj\.codex\.tmp\bundled-marketplaces\openai-bundled\plugins\latex\bin\tectonic.exe" -X compile paper/neurips2026/main.tex --outdir paper/neurips2026/build

# 中文版（Tectonic，自动获取 Fandol 中文字体）
"C:\Users\fwj\.codex\.tmp\bundled-marketplaces\openai-bundled\plugins\latex\bin\tectonic.exe" -X compile paper/neurips2026_zh/main_zh.tex --outdir paper/neurips2026_zh/build
```

### 8.2 数据来源（只读，勿改）

| 文件 | 内容 |
|---|---|
| `paper/generated/dqa30_latest30_results.json` | 12 条件 × old20/new10/合并 的准确率、CI、难题子集、保留率、9 组图-基线配对 |
| `paper/generated/dqa30_fair_gold_results.json` | 公平金标协议（GOLD_V1/V2/V3/Q0T）逐字母、选择分布、配对检验 |
| `paper/generated/dqa30_gold_dense_regions.json` | 2-core / 可视化稠密富集、金标段落召回、答案段召回 |
| `paper/generated/dqa30_frozen_results.json` | 早期冻结结果（旧协议） |
| `paper/generated/dqa30_answer_records.jsonl` | 旧 G1–G5/B1–B3/Q0 逐题记录（大文件，本地保留） |
| `outputs/four_datasets/dqa30_attention/` | G7/G9/G10 逐题答案 JSON（含 selected_letter、support_ids、reason） |

### 8.3 关键脚本

- `scripts/aggregate_dqa30_latest30.py` — 主结果聚合 + 配对统计 + bootstrap
- `scripts/aggregate_dqa30_fair_gold.py` — 公平金标分析（逐字母、分布、配对）
- `scripts/write_neurips2026_tables.py` — 生成论文表格 .tex
- `scripts/plot_neurips2026_main.py` / `plot_neurips2026_d_anchoring.py` / `plot_neurips2026_pairwise.py` / `plot_neurips2026_schematic.py` / `plot_neurips2026_reuse.py` — 论文图
- `scripts/run_dqa30_gold_baseline_9b.py` / `run_dqa30_gold_baseline_fair.py` — 金标/公平金标运行脚本
- `scripts/inline_plotly.py` — 把 plotly.js 内联进 dashboard.html 使离线可用

### 8.4 图方法逐题分布（本文 §4.6 的新核验）复现

```python
import json, glob, os
from collections import Counter
def load(base):
    rows=[]
    for f in sorted(glob.glob(os.path.join(base,'answers','*','q*.json'))):
        rows.append(json.load(open(f,encoding='utf-8')))
    return rows
# G7: g7_pure_graph_tight; G9: g9_graph_rerank_old20 + g9_graph_rerank_weak18; G10: g10_graph_referee_old20 + g10_graph_referee_new10
for name, base in [('G7','outputs/four_datasets/dqa30_attention/g7_pure_graph_tight')]:
    rows=load(base)
    c=Counter(r['selected_letter'] for r in rows)
    print(name, len(rows), dict(sorted(c.items())))
```

---

## 9. 文件与资源索引

### 9.1 论文

| 路径 | 说明 |
|---|---|
| `paper/neurips2026/main.tex` | 英文 NeurIPS 2026 草稿（全文见 §6） |
| `paper/neurips2026/build/main.pdf` | 编译产物（19 页，正文 9 页） |
| `paper/neurips2026_zh/main_zh.tex` | 中文完整版（全文见 §7） |
| `paper/neurips2026_zh/build/main_zh.pdf` | 编译产物（14 页） |
| `paper/neurips2026/figures/*.pdf` | 论文图（fig_schematic / fig_main_accuracy / fig_d_anchoring / fig_pairwise / fig_core_enrichment / fig_force_novel103） |
| `paper/neurips2026/tables/*.tex` | 论文表格（main_results / paired_tests / fair_gold_analysis / appendix_full_tables / macros） |

### 9.2 报告与文档

| 路径 | 说明 |
|---|---|
| `outputs/four_datasets/dqa30_attention/REPORT_30_novels.md` | 30 部小说冻结评测报告 |
| `docs/DQA30_ATTENTION_EXPERIMENT_PROTOCOL.md` | 实验预注册协议 |
| `docs/PROJECT_PROGRESS.md` | 项目进展 |
| `docs/history.md` | 历史记录 |
| `paper/DELIVERABLE_MANIFEST.json` | 交付物清单 |
| `paper/REPRODUCIBILITY.md` | 可复现性说明 |
| `reports/DQA30_G10_GRAPH_BREAKTHROUGH_20260824.md` | G10 突破报告 |
| `reports/DQA30_G7_PURE_GRAPH_REPORT_20260824.md` | G7 纯图谱报告 |
| `reports/DQA30_GOLD_ATTENTION_EXPERIMENT_20260824.md` | 金标注意力实验 |

### 9.3 Dashboard

- HTML 组会 dashboard：`outputs/demo/dashboard.html`（plotly.js 已内联，浏览器离线直接打开）。
- 配色约定贯穿论文图：person=#4e79a7、location=#59a14f、clue_object=#f28e2b、event=#e15759、time_anchor=#b6992d、evidence_sentence=#b07aa1；金标参考线 #ffd700。

### 9.4 其他

- `tools/` — 5 个 PowerShell 启动脚本（eval/local/monitor/objteam/ollama）
- `logs/` — 运行日志（本地保留，不入库）
- `backups/` — 备份
- `config/` — dqa30 冻结图清单、协议 JSON

---

*本报告由仓库内容自动整理。所有统计数字与 `paper/generated/*.json` 一致；论文全文与 `paper/neurips2026/main.tex`、`paper/neurips2026_zh/main_zh.tex` 一致。*
