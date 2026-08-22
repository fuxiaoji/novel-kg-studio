# 未掩码图谱问答突破研究：论文路线、C12 结果与下一步

更新时间：2026-08-11  
数据范围：20 本 DetectiveQA 小说，164 题  
答题模型：`qwen2.5:7b-32k`，实际 `num_ctx=32768`  
约束：全部使用未掩码问题、选项与可用小说范围；不使用 gold 选择答案。

## 1. 验收目标与结果

预设目标满足任一项：

1. 图谱方法准确率达到或超过 50%；
2. 相对尾窗口提高至少 5 个百分点。

C12 未掩码多视角图共识结果：

| 集合 | C12 | 尾窗口 | 差值 |
|---|---:|---:|---:|
| 第一组 10 本 | 49/90（54.4%） | 41/90（45.6%） | +8.9 pp |
| 第二组 10 本 | 37/74（50.0%） | 31/74（41.9%） | +8.1 pp |
| 20 本合并 | **86/164（52.4%）** | 72/164（43.9%） | **+8.5 pp** |

合并逐题为 31 胜、17 负，exact McNemar `p=0.0595`；以小说为簇的 bootstrap 95% CI 为 `[+1.2%, +15.9%]`。数值目标已达到，但由于复合规则是在现有 20 本结果可见后形成，本结果属于探索性结果，仍需新小说确认。

## 2. 论文带来的设计判断

### GraphReader / DocNavRAG：静态一次检索不够

- GraphReader 让 agent 在图上执行读节点、读邻居、记录新信息和反思，核心是“小证据状态 + 粗到细导航”，而不是一次取回大子图。  
  https://arxiv.org/abs/2406.14550
- DocNavRAG 将文档层级和跨区域关系组织为可导航图，并维持持续演化的 evidence state；论文报告相对强基线的答案质量与上下文充分性提升。  
  https://arxiv.org/abs/2608.01565

对本项目的含义：已有 C8/C11 都是静态检索；下一单方法应允许第二轮只沿第一轮证据中出现的实体扩展，同时设置证据充分性停止条件。

### Clue-RAG / GeAR：chunk、知识单元、实体应共同参与

- Clue-RAG 使用 chunk–knowledge unit–entity 多部图，并用 query-driven iterative retrieval 进行受约束扩展。  
  https://arxiv.org/abs/2507.08445
- GeAR 将普通检索器与图扩展结合，再放入多步检索 agent；其重点是用图补出普通检索器无法直接命中的后续 hop。  
  https://aclanthology.org/2025.findings-acl.624/

对本项目的含义：不能只在实体图上传播分数。原文 passage 必须是一等节点；边、实体和 passage 之间需要显式双向映射。

### PathRAG / G-Retriever：需要连通路径，不需要更大的邻域

- PathRAG 认为 GraphRAG 的主要限制往往是冗余而不是不足，用关系路径和 flow-based pruning 减少噪声，并以路径形式组织提示。  
  https://arxiv.org/abs/2502.14902
- G-Retriever 将相关子图选择建模为 Prize-Collecting Steiner Tree，目标是在保留高价值节点的同时控制连通成本。  
  https://arxiv.org/abs/2402.07630

对本项目的含义：C8 的邻居覆盖不应继续扩张。应该只保留连接“问题实体—候选选项实体”的最短高价值路径，并把路径对应原文按推理顺序输出。

### Candidate-Aware Retrieval：选择题必须让选项进入检索目标

- Candidate-Aware Retrieval 同时建模问题和候选答案的相关性，强调 discriminative evidence，并指出过多段落会引入噪声。  
  https://aclanthology.org/2026.findings-acl.435/

对本项目的含义：统一的 question-only passage set 不足以区分四个选项。每个选项需要独立 seed、独立图路径和独立证据包，最后比较“哪个选项被直接支持、哪个被明确反驳”。未检索到不得视为反驳。

### Bridge-guided iterative GraphRAG：桥接证据必须进入前排

- Beyond Static Retrieval 的系统研究发现，迭代有助于多跳问题，但朴素扩展会增加噪声；关键不只是召回桥接证据，还要把它提升到 reader 真正会使用的位置。  
  https://arxiv.org/abs/2509.25530

对本项目的含义：第二轮扩展只允许补足当前推理链缺口，并把桥接 passage 放在提示开头或结尾，不能埋在 18 个段落中间。

### Lost in the Middle：上下文容量不等于上下文利用

- Lost in the Middle 发现相关信息位于上下文开头或结尾时表现最好，位于中间时明显下降；增加文档数量后，reader 收益早于 retriever recall 饱和。  
  https://arxiv.org/abs/2307.03172

对本项目的含义：保留小证据包、路径顺序和首尾重复问题比继续提高 passage 数量更重要。这与本项目 BGE-M3 提升线索召回、最终准确率反降完全一致。

### When to Use Graphs in RAG：图不是所有问题的默认答案

- GraphRAG-Bench 的出发点就是 GraphRAG 在真实任务中经常低于 vanilla RAG，并研究图在哪些层级、多跳与深上下文任务上真正有效。  
  https://arxiv.org/abs/2506.05690

对本项目的含义：保留尾窗口叙事锚点是合理的；图应当负责多跳补洞和选项区分，而不是替换所有顺序文本。

## 3. C12 方法

C12 使用四个未掩码视角：

1. `tail`：末尾叙事锚点；
2. `C4`：有原文引文的图谱检索；
3. `C6`：多图候选证据仲裁；
4. `C8 graph`：与 BM25 相同 passage set 的 verified graph overlay。

四票多数决定答案；2–2 或四方分裂由 C4 裁决。C4 是跨第一组和第二组表现最稳定的单一图方法。决策不使用 gold、题型规则或自报置信度。

稳定性检查：

- C4 平票：86/164（52.4%）；
- 尾窗口平票：83/164（50.6%）；
- 按小说留一选择平票器：每个折都选中 C4，仍为 86/164；
- 因而跨过 50% 并不依赖单个特殊题或唯一平票规则。

## 4. 不能回避的限制

- Qwen-hard 仅为 37/105（35.2%），比尾窗口高 2.9 pp；保守 hard 为 21/77（27.3%），比尾窗口低 5.2 pp。总体提升没有完全转化为更强的去闭卷先验长文本推理。
- C12 组合了历史上不同时间生成的输出；第一批部分 C4 prompt hash 不完全同质。正式论文版本应统一代码版本重跑四通道。
- C12 是探索性选择。现有第二组已经被多轮分析使用，不能再称为严格未见验证集。
- C12 的推理成本高于单方法。它证明互补性可转化为准确率，而不是证明四通道是最终部署形态。

## 5. 下一单方法：C13 选项条件路径状态机

建议将 C12 的互补信息蒸馏为一个图检索/两阶段 reader：

1. 对问题和四个选项分别生成 seed；
2. 每个选项取 2–3 个 sparse/dense passage seed；
3. 只保留连接问题实体与该选项实体的 1–3 跳高价值路径；
4. 路径边必须有可定位原文，弱化 `mentions/related_to/located_at`；
5. 第一轮为每个选项输出直接支持、直接反驳、未知以及引用；
6. 仅对证据缺口执行一次桥接扩展；
7. 最终 reader 只看四份短证据状态，未找到证据保持 unknown；
8. 相关原文按“最强支持在开头、最强反驳在末尾”排列，并在首尾重复问题；
9. 保留尾窗口答案作为 prior，但只有直接路径证据才能推翻；
10. 在新增小说上冻结评估，主指标同时报告 overall、Qwen-hard 和 conservative-hard。

该路线综合了 candidate-aware retrieval、PathRAG、GraphReader/DocNavRAG 和 lost-in-the-middle 的共同结论，目标是用一次受控图检索替代 C12 的多次答题成本。
