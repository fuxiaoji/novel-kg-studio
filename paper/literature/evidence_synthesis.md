# Critical evidence map: graph navigation for long-context narrative QA

Search date: 2026-08-24. Profile: critical evidence map, not a comprehensive systematic review. Sources were restricted to primary papers and official publisher, project, or benchmark pages. The corpus was chosen to test the design rationale and its strongest alternatives; it does not support a claim of exhaustive coverage.

| Work | Primary task and intervention | Evidence relevant to this study | Transfer caveat |
|---|---|---|---|
| Liu et al. (TACL 2024), DOI 10.1162/tacl_a_00638 | Controlled multi-document QA and key-value retrieval; varies evidence position | Long-context performance can fall when relevant material is in the middle, even for nominally long-context models | Mostly multi-document QA, not full-novel multi-hop reasoning |
| Hsieh et al. (Findings ACL 2024), DOI 10.18653/v1/2024.findings-acl.890 | Calibrates positional attention bias | Links lost-in-the-middle behavior to systematic positional bias and reports improved retrieval/RAG after calibration | Model/attention intervention; does not establish that graph retrieval changes causal attention in our model |
| Lewis et al. (NeurIPS 2020) | Dense retrieval plus generation | Establishes parametric/non-parametric memory framing for RAG | Open-domain factual QA differs from one-novel narrative reasoning |
| RAPTOR (ICLR 2024) | Recursive clustering and summaries | Hierarchical compression can integrate evidence at multiple abstraction levels | Reported gains often pair retrieval with stronger answer models; tree is not a relational event graph |
| GraphReader (Findings EMNLP 2024), DOI 10.18653/v1/2024.findings-emnlp.746 | LLM agent plans and traverses a long-text graph with a 4K window | Direct precedent for graph-mediated nonlinear reading under a small context budget | Uses a planning agent and closed GPT-4 components; not a fixed local 9B reader |
| HippoRAG (NeurIPS 2024) | Knowledge graph plus Personalized PageRank | Shows graph diffusion can help multi-hop retrieval and knowledge integration | Benchmark and graph-construction conditions differ from novels; answer-model effects remain possible |
| Edge et al., arXiv:2404.16130 | Entity graph, communities, pre-generated summaries for query-focused summarization | Motivates structured local/global retrieval and graph community context | Main claim concerns global sensemaking/summarization, not multiple-choice narrative evidence questions |
| LongRAG (EMNLP 2024), DOI 10.18653/v1/2024.emnlp-main.1259 | Dual-perspective long-context RAG | Identifies chunk fragmentation and noisy retrieval as weaknesses of vanilla RAG | Includes fine-tuning/system components beyond our ordinary RAG baseline |
| DetectiveQA, arXiv:2409.02465 | 600 bilingual detective-novel questions, average context above 100K tokens | Direct benchmark rationale: dispersed evidence and multi-step narrative reasoning | Our 30-novel subset and graph overlay are not the benchmark's official baseline protocol |

## Synthesis

Three claims are well supported. First, nominal context length is not equivalent to uniform context utilization. Second, retrieval structure matters: ordinary chunks, recursive summaries, and graph traversal expose different evidence units and connectivity. Third, graph-based systems can improve long-input retrieval in some settings.

The stronger claim—graph navigation improves a fixed local 9B model on detective novels beyond tail, ordinary compression, and ordinary vector RAG—cannot be imported from prior work. It requires the paired experiment implemented here. Likewise, a higher gold-node 2-core rate is only a structural association. Without intervention on evidence routing or attention, it cannot demonstrate that the model allocated more causal attention to gold paragraphs.

## Challenge set and disconfirming interpretations

- A tail window may win when decisive evidence is concentrated near the ending.
- Whole-book compression may preserve global plot facts better than sparse graph extraction.
- Vector RAG may outperform a graph when the question is lexically aligned with one decisive passage.
- Graph expansion can add distractors, duplicate evidence, or propagate extraction errors.
- Question-only accuracy may reflect common-sense answerability or benchmark contamination rather than long-text reasoning.
- Different graph-builder cohorts can create apparent method differences; hence the mandatory old20/new10 stratification.

## Safe positioning

The manuscript may claim a controlled empirical comparison under one local 9B model and a frozen 30-novel corpus. It may not claim a universal graph advantage, causal attention improvement, or independent held-out confirmation because graph conditions were developed on portions of the evaluated corpus.
