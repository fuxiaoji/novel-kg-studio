"""Paper-oriented audit of graph effectiveness across the 20+10 DetectiveQA novels.

The first 20 novels are a method-development cohort built with the legacy graph
pipeline.  The last 10 novels are the frozen Qwen3.5-9B/v4 validation cohort.
Results are therefore reported separately.  Pooled values are explicitly marked
as descriptive and are never treated as a same-pipeline confirmatory estimate.
"""

from __future__ import annotations

import csv
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs" / "four_datasets"
NEW_ROOT = BASE / "dqa30_attention" / "batch03_eval"
GRAPH_ROOT = BASE / "dqa30_attention" / "batch03" / "novels"
OLD_C24 = BASE / "dqa_local_c24_pure9_consensus20"
OLD_C16 = BASE / "dqa_local_c16_consensus20"
REPORT_DIR = ROOT / "reports"
METHODS = ("G1", "G2", "G3", "G4", "G5", "B1", "B2", "B3", "Q0")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def wilson(correct: int, total: int, z: float = 1.959963984540054) -> list[float]:
    if total == 0:
        return [0.0, 0.0]
    p = correct / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def score(rows: list[dict[str, Any]], method: str) -> dict[str, Any]:
    correct = sum(row[method] == row["gold"] for row in rows)
    return {
        "correct": correct,
        "total": len(rows),
        "accuracy": correct / len(rows) if rows else 0.0,
        "wilson_95": wilson(correct, len(rows)),
    }


def exact_mcnemar(rows: list[dict[str, Any]], method: str, baseline: str) -> dict[str, Any]:
    wins = sum(row[method] == row["gold"] and row[baseline] != row["gold"] for row in rows)
    losses = sum(row[method] != row["gold"] and row[baseline] == row["gold"] for row in rows)
    n = wins + losses
    if n == 0:
        p = 1.0
    else:
        tail = sum(math.comb(n, index) for index in range(min(wins, losses) + 1)) / (2**n)
        p = min(1.0, 2 * tail)
    return {"wins": wins, "losses": losses, "discordant": n, "exact_p": p}


def clustered_delta(
    rows: list[dict[str, Any]], method: str, baseline: str, samples: int = 10000
) -> list[float]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["novel"]].append(row)
    novels = sorted(grouped)
    rng = random.Random(20260824)
    values: list[float] = []
    for _ in range(samples):
        sample = [item for _ in novels for item in grouped[rng.choice(novels)]]
        a = sum(row[method] == row["gold"] for row in sample) / len(sample)
        b = sum(row[baseline] == row["gold"] for row in sample) / len(sample)
        values.append(a - b)
    values.sort()
    return [values[int(samples * 0.025)], values[int(samples * 0.975)]]


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return numerator / (dx * dy) if dx and dy else None


def load_new_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((NEW_ROOT / "answers").rglob("q*.json")):
        item = load_json(path)
        answers = item["answers"]
        row: dict[str, Any] = {
            "novel": str(item["novel"]),
            "qi": item["qi"],
            "qid": item["qid"],
            "gold": item["gold_letter"],
        }
        row.update({method: answers[method]["selected_letter"] for method in METHODS})
        features = item.get("attention_features") or {}
        row.update(
            {
                "option_order_unanimous": bool(features.get("option_order_unanimous")),
                "graph_retrieved_chunks": int(features.get("graph_retrieved_chunks") or 0),
                "graph_relation_hints": int(features.get("graph_relation_hints") or 0),
                "rag_retrieved_chunks": int(features.get("rag_retrieved_chunks") or 0),
                "g4_route": answers["G4"].get("route"),
                "g5_route": answers["G5"].get("route"),
            }
        )
        rows.append(row)
    if len(rows) != 70:
        raise RuntimeError(f"expected 70 validation questions, got {len(rows)}")
    return rows


def load_old_rows() -> list[dict[str, Any]]:
    q0: dict[str, str] = {}
    with (OLD_C16 / "per_question.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            q0[row["qid"]] = row["closed35"]
    rows: list[dict[str, Any]] = []
    with (OLD_C24 / "per_question.csv").open(encoding="utf-8-sig", newline="") as handle:
        for source in csv.DictReader(handle):
            rows.append(
                {
                    "novel": source["novel"],
                    "qid": source["qid"],
                    "gold": source["gold"],
                    "Q0": q0[source["qid"]],
                    "tail": source["tail"],
                    "graph": source["original"],
                    "reverse": source["reversed"],
                    "cyclic": source["cyclic"],
                    "consensus": source["c24"],
                }
            )
    if len(rows) != 164:
        raise RuntimeError(f"expected 164 development questions, got {len(rows)}")
    return rows


def load_graph_quality() -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = {}
    for novel_dir in sorted(GRAPH_ROOT.iterdir(), key=lambda path: int(path.name)):
        graph_path = novel_dir / "graph.json"
        if not graph_path.exists():
            continue
        graph = load_json(graph_path)
        metrics = dict(((graph.get("quality") or {}).get("metrics") or {}))
        if metrics:
            output[novel_dir.name] = {key: float(value) for key, value in metrics.items()}
    return output


def oracle(rows: list[dict[str, Any]], methods: Iterable[str]) -> dict[str, Any]:
    methods = tuple(methods)
    correct = sum(any(row[method] == row["gold"] for method in methods) for row in rows)
    return {"methods": list(methods), "correct": correct, "total": len(rows), "accuracy": correct / len(rows)}


def pct(value: float) -> str:
    return f"{100 * value:.1f}%"


def fmt_score(item: dict[str, Any]) -> str:
    lo, hi = item["wilson_95"]
    return f"{item['correct']}/{item['total']} ({pct(item['accuracy'])}; 95% CI {pct(lo)}–{pct(hi)})"


def main() -> None:
    new_rows = load_new_rows()
    old_rows = load_old_rows()
    graph_quality = load_graph_quality()
    hard_new = [row for row in new_rows if row["Q0"] != row["gold"]]
    easy_new = [row for row in new_rows if row["Q0"] == row["gold"]]
    hard_old = [row for row in old_rows if row["Q0"] != row["gold"]]

    new_all = {method: score(new_rows, method) for method in METHODS}
    new_hard = {method: score(hard_new, method) for method in METHODS if method != "Q0"}
    new_easy = {method: score(easy_new, method) for method in METHODS if method != "Q0"}
    old_all = {method: score(old_rows, method) for method in ("Q0", "tail", "graph", "reverse", "cyclic", "consensus")}
    old_hard = {method: score(hard_old, method) for method in ("tail", "graph", "reverse", "cyclic", "consensus")}

    comparisons = {}
    for method in ("G1", "G3", "G5"):
        for baseline in ("B1", "B2", "B3"):
            comparisons[f"{method}_vs_{baseline}"] = {
                **exact_mcnemar(new_rows, method, baseline),
                "accuracy_delta": new_all[method]["accuracy"] - new_all[baseline]["accuracy"],
                "novel_cluster_delta_95": clustered_delta(new_rows, method, baseline),
            }

    prediction_agreement = {}
    for method, baseline in (("G1", "B1"), ("G1", "B2"), ("G1", "B3"), ("G5", "B2"), ("G5", "B3")):
        same = sum(row[method] == row[baseline] for row in new_rows)
        prediction_agreement[f"{method}_vs_{baseline}"] = {
            "same_prediction": same,
            "total": len(new_rows),
            "agreement": same / len(new_rows),
            **exact_mcnemar(new_rows, method, baseline),
        }

    stability_groups = {}
    for unanimous in (True, False):
        subset = [row for row in new_rows if row["option_order_unanimous"] is unanimous]
        stability_groups["unanimous" if unanimous else "non_unanimous"] = {
            "questions": len(subset),
            **{method: score(subset, method) for method in ("G1", "G2", "G3", "G5", "B1", "B2", "B3")},
        }

    per_novel = {}
    for novel in sorted({row["novel"] for row in new_rows}, key=int):
        subset = [row for row in new_rows if row["novel"] == novel]
        per_novel[novel] = {
            "questions": len(subset),
            **{method: score(subset, method) for method in ("G1", "G5", "B1", "B2", "B3", "Q0")},
            "quality": graph_quality.get(novel, {}),
        }

    quality_correlations = {}
    for baseline in ("B1", "B2", "B3"):
        deltas = [per_novel[novel]["G1"]["accuracy"] - per_novel[novel][baseline]["accuracy"] for novel in per_novel]
        for metric in ("isolate_rate", "edge_node_ratio", "ungrounded_nodes", "dropped_relation_rate"):
            xs = [per_novel[novel]["quality"].get(metric, 0.0) for novel in per_novel]
            quality_correlations[f"G1_minus_{baseline}__{metric}"] = pearson(xs, deltas)

    total_nodes = sum(item.get("nodes", 0) for item in graph_quality.values())
    total_edges = sum(item.get("edges", 0) for item in graph_quality.values())
    total_isolates = sum(item.get("isolates", 0) for item in graph_quality.values())
    relation_candidates = sum(item.get("relation_candidates", 0) for item in graph_quality.values())
    dropped_relations = sum(item.get("dropped_relations", 0) for item in graph_quality.values())
    graph_aggregate = {
        "novels": len(graph_quality),
        "nodes": total_nodes,
        "edges": total_edges,
        "isolates": total_isolates,
        "micro_isolate_rate": total_isolates / total_nodes,
        "edge_node_ratio": total_edges / total_nodes,
        "dropped_relations": dropped_relations,
        "relation_candidates": relation_candidates,
        "dropped_relation_rate": dropped_relations / relation_candidates,
    }

    descriptive_pool = {
        "Q0": score(
            [{"gold": row["gold"], "pooled": row["Q0"]} for row in old_rows]
            + [{"gold": row["gold"], "pooled": row["Q0"]} for row in new_rows],
            "pooled",
        ),
        "tail": score(
            [{"gold": row["gold"], "pooled": row["tail"]} for row in old_rows]
            + [{"gold": row["gold"], "pooled": row["B1"]} for row in new_rows],
            "pooled",
        ),
        "single_graph": score(
            [{"gold": row["gold"], "pooled": row["graph"]} for row in old_rows]
            + [{"gold": row["gold"], "pooled": row["G1"]} for row in new_rows],
            "pooled",
        ),
        "graph_consensus": score(
            [{"gold": row["gold"], "pooled": row["consensus"]} for row in old_rows]
            + [{"gold": row["gold"], "pooled": row["G5"]} for row in new_rows],
            "pooled",
        ),
    }
    pooled_hard = {
        "tail": score(
            [{"gold": row["gold"], "pooled": row["tail"]} for row in hard_old]
            + [{"gold": row["gold"], "pooled": row["B1"]} for row in hard_new],
            "pooled",
        ),
        "single_graph": score(
            [{"gold": row["gold"], "pooled": row["graph"]} for row in hard_old]
            + [{"gold": row["gold"], "pooled": row["G1"]} for row in hard_new],
            "pooled",
        ),
        "graph_consensus": score(
            [{"gold": row["gold"], "pooled": row["consensus"]} for row in hard_old]
            + [{"gold": row["gold"], "pooled": row["G5"]} for row in hard_new],
            "pooled",
        ),
    }

    diagnostics = {
        "new10_graph_method_oracle": oracle(new_rows, ("G1", "G2", "G3", "G4", "G5")),
        "new10_graph_and_text_oracle": oracle(new_rows, ("G1", "G3", "G5", "B1", "B2", "B3")),
        "new10_core_oracle": oracle(new_rows, ("G1", "B2", "B3")),
        "hard_new10_core_oracle": oracle(hard_new, ("G1", "B2", "B3")),
        "prediction_agreement": prediction_agreement,
        "stability": stability_groups,
        "mean_graph_relation_hints": sum(row["graph_relation_hints"] for row in new_rows) / len(new_rows),
        "mean_graph_retrieved_chunks": sum(row["graph_retrieved_chunks"] for row in new_rows) / len(new_rows),
        "mean_rag_retrieved_chunks": sum(row["rag_retrieved_chunks"] for row in new_rows) / len(new_rows),
    }

    result = {
        "metadata": {
            "generated": "2026-08-24",
            "development": {"novels": 20, "questions": 164, "legacy_graph_builder": True},
            "validation": {"novels": 10, "questions": 70, "graph_builder": "qwen3.5:9b v4"},
            "warning": "Pooled values are descriptive because graph construction and retrieval versions differ.",
        },
        "old20": {"all": old_all, "q0_wrong": old_hard},
        "new10": {
            "all": new_all,
            "q0_wrong": new_hard,
            "q0_correct_preservation": new_easy,
            "paired": comparisons,
            "per_novel": per_novel,
        },
        "descriptive_30": {"all": descriptive_pool, "q0_wrong": pooled_hard},
        "graph_quality_new10": graph_aggregate,
        "quality_correlations_new10": quality_correlations,
        "diagnostics": diagnostics,
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / "DQA30_GRAPH_EFFECTIVENESS_20260824.json"
    csv_path = REPORT_DIR / "DQA30_GRAPH_EFFECTIVENESS_SUMMARY_20260824.csv"
    audit_path = REPORT_DIR / "DQA30_NEW10_QUESTION_AUDIT_20260824.csv"
    report_path = REPORT_DIR / "DQA30_GRAPH_EFFECTIVENESS_REPORT_20260824.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("cohort", "subset", "method", "correct", "total", "accuracy", "ci_low", "ci_high"))
        writer.writeheader()
        for cohort, subset, table in (
            ("old20", "all", old_all),
            ("old20", "q0_wrong", old_hard),
            ("new10", "all", new_all),
            ("new10", "q0_wrong", new_hard),
            ("new10", "q0_correct", new_easy),
            ("descriptive30", "all", descriptive_pool),
            ("descriptive30", "q0_wrong", pooled_hard),
        ):
            for method, item in table.items():
                writer.writerow(
                    {
                        "cohort": cohort,
                        "subset": subset,
                        "method": method,
                        "correct": item["correct"],
                        "total": item["total"],
                        "accuracy": item["accuracy"],
                        "ci_low": item["wilson_95"][0],
                        "ci_high": item["wilson_95"][1],
                    }
                )

    with audit_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ("novel", "qi", "qid", "gold", *METHODS, "option_order_unanimous", "graph_retrieved_chunks", "graph_relation_hints", "rag_retrieved_chunks", "g4_route", "g5_route")
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field) for field in fields} for row in new_rows)

    lines = [
        "# DetectiveQA 30本：图谱方法有效性与优化分析",
        "",
        "## 研究口径",
        "",
        "本分析包含前20本方法开发集的164道题，以及新增10本冻结验证集的70道题。两组均使用Qwen3.5 9B作答，关闭思考并采用未遮蔽输入。前20本使用旧7B建图流程，新增10本使用9B-v4建图流程。因此，验证集是主要证据，30本池化结果仅作描述性参考。",
        "",
        "## 新10本冻结验证结果",
        "",
        "| 方法 | 全量准确率 | Q0错误困难集 | Q0正确题保留率 |",
        "|---|---:|---:|---:|",
    ]
    for method in METHODS:
        hard_text = fmt_score(new_hard[method]) if method in new_hard else "—"
        easy_text = fmt_score(new_easy[method]) if method in new_easy else "—"
        lines.append(f"| {method} | {fmt_score(new_all[method])} | {hard_text} | {easy_text} |")
    lines += [
        "",
        "G1和G5均达到51.4%，较尾窗口B1高10.0个百分点。G1相对B1取得16胜9负，但McNemar检验未显著。按小说聚类的95%区间跨越零。G1仅比普通RAG B3多1题，且与压缩B2持平。因此，图谱相对尾窗口的优势具有实践信号，但尚不能证明其优于更强文本基线。",
        "",
        "在37道Q0错误题上，B2达到37.8%，G3和G5达到35.1%。G1与B3均为32.4%。这说明图谱确实能纠正部分无上下文错误，但当前单次图谱注入没有超过压缩或普通RAG。",
        "",
        "## 前20本开发集与描述性30本结果",
        "",
        "| 开发集方法 | 正确率 | Q0错误困难集 |",
        "|---|---:|---:|",
    ]
    for method in ("Q0", "tail", "graph", "reverse", "cyclic", "consensus"):
        hard_text = fmt_score(old_hard[method]) if method in old_hard else "—"
        lines.append(f"| {method} | {fmt_score(old_all[method])} | {hard_text} |")
    lines += [
        "",
        "前20本中，纯9B三排列共识达到53.7%，高于尾窗口5.5个百分点。其优势未通过逐题显著性检验。新增10本中，对应G5达到51.4%，高于尾窗口10.0个百分点，但与B2持平。两批结果方向一致，支持图谱对尾窗口存在有限增益。",
        "",
        "| 描述性30本方法族 | 正确率 | Q0错误困难集 |",
        "|---|---:|---:|",
    ]
    for method in ("Q0", "tail", "single_graph", "graph_consensus"):
        hard_text = fmt_score(pooled_hard[method]) if method in pooled_hard else "—"
        lines.append(f"| {method} | {fmt_score(descriptive_pool[method])} | {hard_text} |")
    lines += [
        "",
        "上述30本合计不能作为正式同流程主结果。图谱构建器与检索提示在两批之间不同。论文应将20本标为开发集，并将新增10本标为冻结外部验证集。",
        "",
        "## 互补性与失败机制",
        "",
        f"G1、B2和B3三者的逐题oracle为{diagnostics['new10_core_oracle']['correct']}/70（{pct(diagnostics['new10_core_oracle']['accuracy'])}）。困难集oracle为{diagnostics['hard_new10_core_oracle']['correct']}/37（{pct(diagnostics['hard_new10_core_oracle']['accuracy'])}）。该差距说明方法间存在可利用的互补性，但需要不读取金标的选择器。",
        "",
        f"G1与B3的正确性比较只有{comparisons['G1_vs_B3']['wins']}胜和{comparisons['G1_vs_B3']['losses']}负。当前G1沿用B3的原文段落，仅增加图关系提示。图谱因此很少改变最终判断。该结果表明主要瓶颈是图谱没有扩展证据范围，而不是图节点数量不足。",
        "",
        "三个选项排列的性能差异较大。原序G1为51.4%，逆序G2为44.3%，循环序G3为48.6%。多数门控G5未超过G1。模型仍受选项位置影响，简单多数投票不能稳定提取图谱收益。",
        "",
        "## 图谱质量与准确率",
        "",
        f"新增10本图谱合计包含{int(total_nodes)}个节点和{int(total_edges)}条边。微平均孤立率为{pct(graph_aggregate['micro_isolate_rate'])}，边节点比为{graph_aggregate['edge_node_ratio']:.2f}，关系丢弃率为{pct(graph_aggregate['dropped_relation_rate'])}。这些结构指标显著优于旧图，但高结构质量没有自动转化为相对B3的准确率优势。",
        "",
        "仅有10个小说级观测，图质量与准确率差值的相关系数不稳定。它们只能用于生成假设，不能解释因果。现有抽查还发现共指自环和错误合并，因此语义质量门仍需加强。",
        "",
        "## 可验证的图谱优化路线",
        "",
        "第一条路线是图引导的证据扩展。系统应先从选项实体进入图谱，再沿时间、动机、手段、机会、支持和反驳关系扩展一至两跳。每条路径必须回到原文证据。扩展得到的新段落应替换低价值RAG段落，并保持与B3相同的总字符预算。该设计可以直接检验图谱是否带来新的证据。",
        "",
        "第二条路线是选项级有符号证据图。系统应分别构建四个候选答案的支持链与反驳链。边分数应同时考虑原文落地、叙事终局位置、关系类型、实体别名置信度和跨段一致性。模型接收紧凑证据表，而不是无差别的关系列表。",
        "",
        "第三条路线是选择性图谱路由。选择器只使用无金标特征，包括图关系支持差距、证据落地率、排列一致性、G1与B3分歧、终局段落覆盖和共指风险。选择器应只在前20本训练和定阈值，然后在新增10本冻结验证。目标是在图谱证据强时采用图方法，否则回退B2或B3。",
        "",
        "第四条路线是反事实消融。对每题逐条删除图关系，并记录答案是否翻转。真正有帮助的关系应提高正确选项的稳定性。若删除关系不影响答案，则该关系不应占用上下文预算。此实验比把Ollama输出解释为内部attention更严谨。",
        "",
        "第五条路线是语义质量控制。合并阶段应拒绝人物自环、类型不兼容的端点替换和缺少原文证据的共指迁移。图检索时应降低高风险关系权重。该修改可以利用已有Pass1和Pass2缓存重跑，无需重新调用建图模型。",
        "",
        "## 论文结论边界",
        "",
        "现有数据支持一个谨慎结论：图谱增强方法在两个批次中均高于尾窗口，并在新增10本达到10个百分点的数值增益。然而，该增益尚未达到统计显著，也没有超过压缩与普通RAG。当前证据因此证明图谱具有互补潜力，而非证明图谱全面优越。下一轮实验应把主比较设为图引导扩展对B3，并使用相同token预算。",
        "",
        "正式30本同流程结论仍需将前20本用9B-v4重新建图和评估。完成后，应预注册G1-B3或新图扩展方法-B3为主比较，并同时报告全量、Q0错误困难集、逐题McNemar和按小说聚类区间。",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "json": str(json_path), "summary_csv": str(csv_path), "audit_csv": str(audit_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
