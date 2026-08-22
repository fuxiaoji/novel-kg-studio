"""Combine the two 10-novel Option-C validation batches into one auditable report."""

from __future__ import annotations

import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scipy.stats import binomtest, spearmanr

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs" / "four_datasets"
OUT = BASE / "dqa_qwen_c_combined20"
METHODS = ["tail", "c1", "c2", "c4", "c3first", "c6"]
MASKS = ["masked", "unmasked"]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load_rows() -> list[dict[str, Any]]:
    first = read_csv(BASE / "dqa_qwen_c_improvements" / "per_question_matrix.csv")
    first_next = read_csv(BASE / "dqa_qwen_c_next_round" / "per_question_matrix.csv")
    next_by_key = {(r["novel"], r["qi"]): r for r in first_next}
    rows: list[dict[str, Any]] = []
    for row in first:
        nxt = next_by_key[(row["novel"], row["qi"])]
        out: dict[str, Any] = {k: row[k] for k in ("novel", "qi", "qid", "question_type", "question", "gold_letter")}
        out["batch"] = "first10"
        for mask in MASKS:
            out[f"tail_{mask}"] = row[f"old_tail_{mask}"]
            for method in ("c1", "c2", "c4", "c6"):
                out[f"{method}_{mask}"] = row[f"{method}_{mask}"]
            out[f"c3first_{mask}"] = nxt[f"c3stage1_{mask}"]
        rows.append(out)
    second = read_csv(BASE / "dqa_qwen_c_next10_methods" / "per_question_matrix.csv")
    for row in second:
        out = {k: row[k] for k in ("novel", "qi", "qid", "question_type", "question", "gold_letter")}
        out["batch"] = "second10"
        for mask in MASKS:
            for method in METHODS:
                out[f"{method}_{mask}"] = row[f"{method}_{mask}"]
        rows.append(out)
    return rows


def correct(row: dict[str, Any], method: str, mask: str) -> bool:
    return row[f"{method}_{mask}"] == row["gold_letter"]


def mcnemar(new: list[bool], base: list[bool]) -> dict[str, Any]:
    wins = sum(n and not b for n, b in zip(new, base))
    losses = sum(b and not n for n, b in zip(new, base))
    p = float(binomtest(min(wins, losses), wins + losses, 0.5).pvalue) if wins + losses else 1.0
    return {"wins": wins, "losses": losses, "discordant": wins + losses, "p_raw": p}


def holm(items: list[dict[str, Any]]) -> None:
    ordered = sorted(enumerate(items), key=lambda pair: pair[1]["mcnemar"]["p_raw"])
    running = 0.0
    for rank, (index, item) in enumerate(ordered):
        adjusted = min(1.0, item["mcnemar"]["p_raw"] * (len(items) - rank))
        running = max(running, adjusted)
        items[index]["mcnemar"]["p_holm"] = running


def bootstrap(rows: list[dict[str, Any]], new: list[bool], base: list[bool], iterations: int = 10000) -> list[float]:
    rng = random.Random(20260811)
    novels = list(dict.fromkeys(r["novel"] for r in rows))
    by_novel = {novel: [i for i, row in enumerate(rows) if row["novel"] == novel] for novel in novels}
    deltas: list[float] = []
    for _ in range(iterations):
        sample: list[int] = []
        for _ in novels:
            novel = rng.choice(novels)
            ids = by_novel[novel]
            sample.extend(rng.choice(ids) for _ in ids)
        deltas.append(sum(int(new[i]) - int(base[i]) for i in sample) / len(sample))
    deltas.sort()
    return [deltas[int(iterations * 0.025)], deltas[min(int(iterations * 0.975), iterations - 1)]]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for mask in MASKS:
        vectors = {m: [correct(r, m, mask) for r in rows] for m in METHODS}
        summary = {
            m: {"correct": sum(v), "total": len(rows), "accuracy": sum(v) / len(rows), "parsed": sum(bool(r[f"{m}_{mask}"]) for r in rows)}
            for m, v in vectors.items()
        }
        comparisons = []
        for method in METHODS[1:]:
            delta = summary[method]["accuracy"] - summary["tail"]["accuracy"]
            comparisons.append({
                "method": method,
                "baseline": "tail",
                "delta": delta,
                "bootstrap_95": bootstrap(rows, vectors[method], vectors["tail"]),
                "mcnemar": mcnemar(vectors[method], vectors["tail"]),
            })
        holm(comparisons)
        oracle = sum(any(vectors[m][i] for m in METHODS) for i in range(len(rows)))
        graph_oracle = sum(any(vectors[m][i] for m in METHODS[1:]) for i in range(len(rows)))
        tail_only = sum(vectors["tail"][i] and not any(vectors[m][i] for m in METHODS[1:]) for i in range(len(rows)))
        graph_rescue = sum(not vectors["tail"][i] and any(vectors[m][i] for m in METHODS[1:]) for i in range(len(rows)))
        result[mask] = {
            "summary": summary,
            "comparisons": comparisons,
            "oracle": {"correct": oracle, "total": len(rows)},
            "graph_oracle": {"correct": graph_oracle, "total": len(rows)},
            "tail_only": tail_only,
            "graph_rescue": graph_rescue,
        }
    return result


def breakdown(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[field])].append(row)
    result: dict[str, Any] = {}
    for key, group in sorted(grouped.items()):
        result[key] = {mask: {m: {"correct": sum(correct(r, m, mask) for r in group), "total": len(group)} for m in METHODS} for mask in MASKS}
    return result


def mask_effect(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for method in METHODS:
        masked = [correct(r, method, "masked") for r in rows]
        unmasked = [correct(r, method, "unmasked") for r in rows]
        stat = mcnemar(unmasked, masked)
        result[method] = {
            "masked_accuracy": sum(masked) / len(rows),
            "unmasked_accuracy": sum(unmasked) / len(rows),
            "delta": (sum(unmasked) - sum(masked)) / len(rows),
            "mcnemar": stat,
        }
    return result


def graph_stats() -> dict[str, Any]:
    roots = {"first10": BASE / "dqa_qwen_c" / "novels", "second10": BASE / "dqa_qwen_c_next10" / "novels"}
    result: dict[str, Any] = {"novels": {}}
    for batch, root in roots.items():
        for path in sorted(root.glob("*/graph.json")):
            graph = json.loads(path.read_text(encoding="utf-8"))
            nodes, edges = graph.get("nodes", []), graph.get("edges", [])
            result["novels"][path.parent.name] = {
                "batch": batch,
                "nodes": len(nodes),
                "edges": len(edges),
                "edge_node_ratio": len(edges) / len(nodes) if nodes else 0.0,
                "isolates": sum(int(n.get("degree", 0)) == 0 for n in nodes),
            }
    for batch in roots:
        values = [v for v in result["novels"].values() if v["batch"] == batch]
        result[batch] = {
            "novels": len(values),
            "nodes": sum(v["nodes"] for v in values),
            "edges": sum(v["edges"] for v in values),
            "average_nodes": sum(v["nodes"] for v in values) / len(values),
            "average_edges": sum(v["edges"] for v in values) / len(values),
            "isolate_rate": sum(v["isolates"] for v in values) / sum(v["nodes"] for v in values),
        }
    all_values = list(result["novels"].values())
    result["combined"] = {
        "novels": len(all_values),
        "nodes": sum(v["nodes"] for v in all_values),
        "edges": sum(v["edges"] for v in all_values),
        "isolate_rate": sum(v["isolates"] for v in all_values) / sum(v["nodes"] for v in all_values),
    }
    return result


def graph_accuracy_correlation(rows: list[dict[str, Any]], graphs: dict[str, Any]) -> dict[str, Any]:
    novels = sorted(graphs["novels"])
    result = {}
    for mask in MASKS:
        for method in METHODS[1:]:
            sizes, ratios, accuracies = [], [], []
            for novel in novels:
                group = [r for r in rows if r["novel"] == novel]
                if not group:
                    continue
                sizes.append(graphs["novels"][novel]["nodes"])
                ratios.append(graphs["novels"][novel]["edge_node_ratio"])
                accuracies.append(sum(correct(r, method, mask) for r in group) / len(group))
            rho_size, p_size = spearmanr(sizes, accuracies)
            rho_ratio, p_ratio = spearmanr(ratios, accuracies)
            result[f"{method}/{mask}"] = {"nodes_rho": float(rho_size), "nodes_p": float(p_size), "edge_node_ratio_rho": float(rho_ratio), "edge_node_ratio_p": float(p_ratio)}
    return result


def distributions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = {"gold": dict(Counter(r["gold_letter"] for r in rows))}
    for mask in MASKS:
        for method in METHODS:
            result[f"{method}/{mask}"] = dict(Counter(r[f"{method}_{mask}"] or "unparsed" for r in rows))
    return result


def pct(value: float) -> str:
    return f"{value:.1%}"


def make_report(report: dict[str, Any]) -> str:
    lines = [
        "# 方案 C：20 本 DetectiveQA 小说综合分析报告",
        "",
        "## 1. 执行摘要",
        "",
        "本报告合并第一批 10 本（90 题）与第二批 10 本外部验证（74 题），共 20 本、164 道选择题。两批均使用 `qwen2.5:7b-32k`。",
        "",
        "核心结论：在公平的 unmasked 比较中，单一图谱方法没有稳定、显著地超过尾窗口基线。20 本合并后 C4 为最高单方法（74/164，45.1%），仅比尾窗口高 2 题（+1.2 个百分点）。masked 表中的 tail 实际仍从未遮蔽源小说截取末尾约 5 万字符，不能作为公平的遮蔽基线；在受同等遮蔽约束的图谱方法中，C4 与 C6 并列最高（65/164，39.6%）。",
        "",
        "图谱方法的主要价值来自互补性而非单模型平均准确率：不同方法的候选 oracle 明显高于任一单方法，但当前 C6 仲裁没有把这种上限稳定转化为实际收益。",
        "",
        "## 2. 实验口径与可比性",
        "",
        "- 第一批小说：26、27、28、30、31、33、40、53、56、79，共 90 题。",
        "- 第二批小说：15、16、25、29、81、82、83、84、87、90，共 74 题。",
        "- 比较方法：tail、C1、C2、C4、C3-first（第一批名为 c3stage1）、C6。",
        "- 对图谱方法，masked 表示只允许从遮蔽点之前检索，unmasked 表示可从全书范围检索；两者都只把检索到的有限证据片段送入 LLM，并未把整本小说塞入上下文。",
        "- 重要限制：tail 实现没有应用 `mask_char`，masked 与 unmasked 均从未遮蔽源小说截取末尾约 5 万字符，因此两列完全相同。masked-vs-tail 的差值和检验仅作描述，不能解释为公平的遮蔽条件优劣。",
        "- 第一批部分 C2/C4 结果的 prompt hash 不完全同质，因此合并结果应解读为跨批次外部验证汇总，而不是完全同版本的严格重复实验。C1 masked、C6 两种 mask 版本审计同质。",
        "",
    ]
    for scope in ("first10", "second10", "combined"):
        title = {"first10": "第一批 10 本", "second10": "第二批 10 本", "combined": "20 本合并"}[scope]
        lines += [f"## {3 if scope == 'first10' else 4 if scope == 'second10' else 5}. {title}", ""]
        block = report["batches"][scope] if scope != "combined" else report["combined"]
        for mask in MASKS:
            lines += [f"### {mask}", "", "| 方法 | 正确/总数 | 准确率 | 相对 tail | Holm p | Bootstrap 95% CI |", "|---|---:|---:|---:|---:|---:|"]
            comps = {x["method"]: x for x in block[mask]["comparisons"]}
            for method in sorted(METHODS, key=lambda m: -block[mask]["summary"][m]["accuracy"]):
                s = block[mask]["summary"][method]
                if method == "tail":
                    lines.append(f"| {method} | {s['correct']}/{s['total']} | {pct(s['accuracy'])} | — | — | — |")
                else:
                    c = comps[method]
                    lo, hi = c["bootstrap_95"]
                    lines.append(f"| {method} | {s['correct']}/{s['total']} | {pct(s['accuracy'])} | {c['delta']:+.1%} | {c['mcnemar']['p_holm']:.4f} | [{lo:+.1%}, {hi:+.1%}] |")
            lines += ["", f"六方法 oracle：{block[mask]['oracle']['correct']}/{block[mask]['oracle']['total']}（{pct(block[mask]['oracle']['correct']/block[mask]['oracle']['total'])}）；图谱方法可挽救 tail 错题 {block[mask]['graph_rescue']} 道，但也有 {block[mask]['tail_only']} 道仅 tail 正确。", ""]
    lines += [
        "## 6. 遮蔽范围与未遮蔽检索的影响",
        "",
        "| 方法 | masked | unmasked | 变化 | McNemar p |",
        "|---|---:|---:|---:|---:|",
    ]
    for method, item in report["mask_effect"].items():
        lines.append(f"| {method} | {pct(item['masked_accuracy'])} | {pct(item['unmasked_accuracy'])} | {item['delta']:+.1%} | {item['mcnemar']['p_raw']:.4f} |")
    g = report["graphs"]
    lines += [
        "",
        "## 7. 图谱规模与质量信号",
        "",
        "| 批次 | 小说 | 节点 | 边 | 平均节点/本 | 平均边/本 | 孤立节点率 |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| 第一批 | {g['first10']['novels']} | {g['first10']['nodes']} | {g['first10']['edges']} | {g['first10']['average_nodes']:.1f} | {g['first10']['average_edges']:.1f} | {pct(g['first10']['isolate_rate'])} |",
        f"| 第二批 | {g['second10']['novels']} | {g['second10']['nodes']} | {g['second10']['edges']} | {g['second10']['average_nodes']:.1f} | {g['second10']['average_edges']:.1f} | {pct(g['second10']['isolate_rate'])} |",
        f"| 合计 | {g['combined']['novels']} | {g['combined']['nodes']} | {g['combined']['edges']} | — | — | {pct(g['combined']['isolate_rate'])} |",
        "",
        "第二批图谱明显更大，但大多数图谱方法在第二批准确率反而下降。这说明增加节点和边本身不足以改善问答；检索精度、实体规范化、证据定位和噪声控制比规模更关键。20 本小说层面的 Spearman 相关分析未显示图谱节点数能稳定预测准确率（详见 analysis.json）。",
        "",
        "## 8. 题型与小说差异",
        "",
        "以下仅列样本数不少于 5 的题型，并给出 unmasked 条件下最佳方法：",
        "",
        "| 题型 | 题数 | 最佳方法 | 正确率 |",
        "|---|---:|---|---:|",
    ]
    for qtype, block in report["by_type"].items():
        total = next(iter(block["unmasked"].values()))["total"]
        if total < 5:
            continue
        best = max(METHODS, key=lambda m: block["unmasked"][m]["correct"])
        value = block["unmasked"][best]
        lines.append(f"| {qtype} | {total} | {best} | {value['correct']}/{value['total']} ({pct(value['correct']/value['total'])}) |")
    lines += [
        "",
        "小说级结果波动很大，单批 10 本容易产生方法排序反转。第二批外部验证中，第一批领先的 C6 unmasked 从 50.0% 降至 36.5%；C4 unmasked 则从 45.6% 保持到 44.6%，是跨批次最稳定的图谱方法。",
        "",
        "## 9. 误差结构与仲裁",
        "",
        "- 所有 1036 个第二批方法×mask×问题结果均成功解析，说明结构化输出可靠性已经解决。",
        "- C6 在第一批 unmasked 达到 50.0%，但第二批只有 36.5%，表明仲裁器对候选分布和小说域变化敏感。",
        "- oracle 与单方法之间存在较大差距，说明候选间确有互补信息；当前主要瓶颈是识别哪一个候选在当前问题上可信，而不是缺少候选答案。",
        "- tail 在 masked 表中持续强势主要因为它未实际遮蔽、仍可看到后文；该结果反映信息权限差异，不能归因于方法本身。图谱方法内部比较仍提示检索噪声和早期诱饵可能稀释最终证据。",
        "",
        "## 10. 结论与后续建议",
        "",
        "1. 当前不能声称知识图谱方法显著优于尾窗口基线；所有相对 tail 的 Holm 校正检验均未达到 0.05。",
        "2. 若只选一个可部署方案：允许从全书范围检索的场景可选 C4，但其合并优势仅 +1.2 个百分点，应视为与 tail 持平；严格遮蔽场景应在 C4/C6 之间选择，不能使用当前未遮蔽尾窗口数字作基线。",
        "3. 下一轮优先改进检索与仲裁，而不是继续扩大图谱：限制每题证据预算、按时间靠近结局加权、对诱饵边降权、要求选项级直接引文。",
        "4. 仲裁训练必须采用按小说留一验证，避免在同一批小说上选择方法后再报告性能。",
        "5. 建议保留 tail+C4 双通道，并训练一个只使用可审计特征的门控器：直接证据数、选项间矛盾数、证据时间位置、引用覆盖率、候选一致性；不使用模型自报置信度。",
        "6. 对第一批混合 prompt hash 的 C2/C4 应择一版本重跑，才能形成严格同版本的 20 本最终论文表。",
        "",
        "## 11. 产物",
        "",
        "- `analysis.json`：完整统计、显著性、题型/小说分解、图谱相关性。",
        "- `per_question_matrix.csv`：164 题逐题预测矩阵。",
        "- 本报告：`REPORT.md`。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    rows = load_rows()
    if len(rows) != 164 or len({r["qid"] for r in rows}) != 164:
        raise RuntimeError(f"expected 164 unique questions, got {len(rows)} rows / {len({r['qid'] for r in rows})} qids")
    for row in rows:
        for mask in MASKS:
            for method in METHODS:
                if row[f"{method}_{mask}"] not in {"", "A", "B", "C", "D"}:
                    raise RuntimeError(f"invalid prediction: {row['qid']} {method}/{mask}")
    batches = {batch: summarize([r for r in rows if r["batch"] == batch]) for batch in ("first10", "second10")}
    combined = summarize(rows)
    graphs = graph_stats()
    report = {
        "metadata": {"questions": len(rows), "novels": len({r['novel'] for r in rows}), "methods": METHODS, "masks": MASKS, "model": "qwen2.5:7b-32k"},
        "batches": batches,
        "combined": combined,
        "mask_effect": mask_effect(rows),
        "by_type": breakdown(rows, "question_type"),
        "by_novel": breakdown(rows, "novel"),
        "graphs": graphs,
        "graph_accuracy_correlation": graph_accuracy_correlation(rows, graphs),
        "prediction_distributions": distributions(rows),
        "comparability_notes": [
            "First-batch C2/C4 prompt hashes are not fully homogeneous.",
            "c3stage1 in batch 1 is mapped to c3first in batch 2 as the single-stage contrastive variant.",
            "Both batches use qwen2.5:7b-32k and the same masked/unmasked definitions.",
            "Tail ignores mask_char and reads the terminal ~50k-character window from the unmasked source novel in both mask labels; masked tail comparisons are descriptive, not fair masked baselines.",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    fields = ["batch", "novel", "qi", "qid", "question_type", "question", "gold_letter"] + [f"{m}_{mask}" for mask in MASKS for m in METHODS]
    with (OUT / "per_question_matrix.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: row[k] for k in fields} for row in rows)
    (OUT / "analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "REPORT.md").write_text(make_report(report), encoding="utf-8")
    print(OUT / "REPORT.md")


if __name__ == "__main__":
    main()
