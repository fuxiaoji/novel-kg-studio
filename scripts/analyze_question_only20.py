"""Analyze the question-only baseline and evidence-required hard subset."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scipy.stats import binomtest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs" / "four_datasets"
COMBINED = BASE / "dqa_qwen_c_combined20"
QONLY = BASE / "dqa_qwen_question_only20"
METHODS = ["tail", "c1", "c2", "c4", "c3first", "c6"]
GRAPH_CORE = ["c1", "c2", "c4", "c3first"]
GRAPH_ALL = ["c1", "c2", "c4", "c3first", "c6"]
MASKS = ["masked", "unmasked"]
LETTERS = ["A", "B", "C", "D"]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [dict(r) for r in read_csv(COMBINED / "per_question_matrix.csv")]
    qonly = {}
    for path in (QONLY / "answers").glob("*/*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        qonly[data["qid"]] = data
    if len(qonly) != len(rows):
        raise RuntimeError(f"question-only rows mismatch: {len(qonly)} vs {len(rows)}")
    for row in rows:
        baseline = qonly[row["qid"]]
        row["choices"] = baseline["choices"]
        row["qonly_letter"] = baseline["selected_letter"]
        row["qonly_correct"] = baseline["selected_letter"] == row["gold_letter"]
        row["qonly_reason"] = baseline.get("reason", "")
    return rows


def is_correct(row: dict[str, Any], method: str, mask: str) -> bool:
    return row[f"{method}_{mask}"] == row["gold_letter"]


def vote(row: dict[str, Any], methods: list[str], mask: str, tie: str = "c4") -> str:
    counts = Counter(row[f"{m}_{mask}"] for m in methods if row.get(f"{m}_{mask}") in LETTERS)
    if not counts:
        return row[f"{tie}_{mask}"]
    top = max(counts.values())
    winners = {letter for letter, count in counts.items() if count == top}
    tied = row.get(f"{tie}_{mask}")
    return tied if tied in winners else sorted(winners)[0]


def consensus_gate(row: dict[str, Any], mask: str, threshold: int) -> str:
    counts = Counter(row[f"{m}_{mask}"] for m in GRAPH_CORE)
    letter, count = counts.most_common(1)[0]
    return letter if count >= threshold else row[f"tail_{mask}"]


def training_accuracy(train: list[dict[str, Any]], method: str, mask: str, qtype: str | None = None) -> float:
    pool = [r for r in train if qtype is None or r["question_type"] == qtype]
    if not pool:
        return 0.25
    correct = sum(is_correct(r, method, mask) for r in pool)
    return (correct + 2.0) / (len(pool) + 8.0)


def loo_predictions(rows: list[dict[str, Any]], methods: list[str], mask: str, by_type: bool, weighted: bool) -> list[str]:
    out = []
    for row in rows:
        train = [r for r in rows if r["novel"] != row["novel"]]
        qtype = row["question_type"] if by_type else None
        weights = {m: training_accuracy(train, m, mask, qtype) for m in methods}
        if weighted:
            scores = {letter: 0.0 for letter in LETTERS}
            for method, weight in weights.items():
                letter = row[f"{method}_{mask}"]
                if letter in scores:
                    scores[letter] += max(0.0, weight - 0.25)
            best = max(scores.values())
            winners = {letter for letter, score in scores.items() if score == best}
            c4 = row[f"c4_{mask}"]
            out.append(c4 if c4 in winners else sorted(winners)[0])
        else:
            chosen = max(methods, key=lambda m: (weights[m], -methods.index(m)))
            out.append(row[f"{chosen}_{mask}"])
    return out


def mcnemar(pred: list[str], base: list[str], rows: list[dict[str, Any]]) -> dict[str, Any]:
    new = [p == r["gold_letter"] for p, r in zip(pred, rows)]
    old = [p == r["gold_letter"] for p, r in zip(base, rows)]
    wins = sum(n and not b for n, b in zip(new, old))
    losses = sum(b and not n for n, b in zip(new, old))
    p = float(binomtest(min(wins, losses), wins + losses, 0.5).pvalue) if wins + losses else 1.0
    return {"wins": wins, "losses": losses, "p_raw": p}


def evaluate_predictions(rows: list[dict[str, Any]], predictions: dict[str, list[str]], baseline: str = "tail") -> dict[str, Any]:
    result = {}
    base = predictions[baseline]
    for method, values in predictions.items():
        correct = sum(v == r["gold_letter"] for v, r in zip(values, rows))
        result[method] = {"correct": correct, "total": len(rows), "accuracy": correct / len(rows)}
        if method != baseline:
            result[method]["vs_tail"] = mcnemar(values, base, rows)
    return result


def ensemble_predictions(rows: list[dict[str, Any]], mask: str) -> dict[str, list[str]]:
    predictions = {m: [r[f"{m}_{mask}"] for r in rows] for m in METHODS}
    predictions.update(
        {
            "majority_core": [vote(r, GRAPH_CORE, mask) for r in rows],
            "majority_graph_all": [vote(r, GRAPH_ALL, mask) for r in rows],
            "consensus4_else_tail": [consensus_gate(r, mask, 4) for r in rows],
            "consensus3_else_tail": [consensus_gate(r, mask, 3) for r in rows],
            "loo_best_graph": loo_predictions(rows, GRAPH_ALL, mask, False, False),
            "loo_type_best_graph": loo_predictions(rows, GRAPH_ALL, mask, True, False),
            "loo_weighted_graph": loo_predictions(rows, GRAPH_ALL, mask, False, True),
            "loo_type_weighted_graph": loo_predictions(rows, GRAPH_ALL, mask, True, True),
            "loo_weighted_all": loo_predictions(rows, METHODS, mask, False, True),
        }
    )
    return predictions


def by_group(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row[field])].append(row)
    result = {}
    for key, group in sorted(groups.items()):
        result[key] = {
            "total": len(group),
            "question_only": sum(r["qonly_correct"] for r in group),
            "hard": sum(not r["qonly_correct"] for r in group),
            "unmasked": {m: sum(is_correct(r, m, "unmasked") for r in group) for m in METHODS},
            "hard_unmasked": {m: sum(is_correct(r, m, "unmasked") for r in group if not r["qonly_correct"]) for m in METHODS},
        }
    return result


def history() -> dict[str, Any]:
    return {
        "old_graph_7novels_59q": {
            "v4_masked": 0.339,
            "v4_unmasked": 0.339,
            "v5a_masked": 0.339,
            "v5a_unmasked": 0.322,
            "v5b_masked": 0.441,
            "v5b_unmasked": 0.322,
            "v7_masked": 0.390,
            "v7_unmasked": 0.441,
            "vote_masked": 0.407,
            "vote_unmasked": 0.397,
            "tail": 0.356,
            "compress": 0.424,
            "gold_masked": 0.508,
            "gold_unmasked": 0.542,
        },
        "scheme_c_first10_90q_initial": {
            "tail": 0.433,
            "compress": 0.433,
            "best_graph_v5a": 0.433,
            "gold_masked": 0.544,
            "gold_unmasked": 0.556,
        },
    }


def pct(x: float) -> str:
    return f"{x:.1%}"


def report_md(report: dict[str, Any]) -> str:
    q = report["question_only"]
    lines = [
        "# 20 本小说：题目先验基线、去先验硬集与突破路线分析",
        "",
        "## 1. 关键结论",
        "",
        f"只给 Qwen2.5-7B-32K 题目和四个选项，不提供任何小说或图谱信息，得到 {q['correct']}/{q['total']}（{pct(q['accuracy'])}）。据此排除这 {q['correct']} 道题，剩余证据硬集 {q['hard_total']} 题。",
        "",
        "题目-only 达到 36.0%，明显高于四选一随机水平，说明数据中存在可利用的题目措辞、选项语义先验、常识或潜在训练记忆。后续方法应同时报告全量准确率和去先验硬集准确率。",
        "",
        "## 2. 题目-only 基线",
        "",
        "| 批次 | 正确/总数 | 准确率 |",
        "|---|---:|---:|",
    ]
    for batch, value in report["question_only_by_batch"].items():
        lines.append(f"| {batch} | {value['correct']}/{value['total']} | {pct(value['accuracy'])} |")
    lines += [
        "",
        "### 各方法的上下文口径",
        "",
        "- **question-only**：只输入题目与四个选项，不含小说、图谱、题号元数据或证据。",
        "- **tail**：输入未遮蔽源小说的末尾约 5 万字符；不是整本小说。当前实现忽略 `mask_char`，所以 masked/unmasked 数字相同。",
        "- **compress**：先把全书压缩为约 1–1.3 万字符的摘要，再输入模型；它是全书压缩记忆，不是全量原文。",
        "- **图方法**：masked 只允许从遮蔽点前检索，unmasked 可从全书范围检索；实际输入模型的仍是有预算上限的检索片段。",
        "- **gold**：输入人工整理的金标证据，用于估计证据充分时的上限，也不是整本原文。",
        "",
        "### 题型与选项偏置",
        "",
        "| 题型 | question-only 正确/总数 | 准确率 | 硬集题数 |",
        "|---|---:|---:|---:|",
    ]
    for qtype, value in report["question_only_by_type"].items():
        lines.append(f"| {qtype} | {value['question_only']}/{value['total']} | {pct(value['question_only'] / value['total'])} | {value['hard']} |")
    pred = report["qonly_prediction_distribution"]
    gold = report["qonly_gold_distribution"]
    lines += [
        "",
        f"预测分布 A/B/C/D = {pred.get('A', 0)}/{pred.get('B', 0)}/{pred.get('C', 0)}/{pred.get('D', 0)}；金标分布 = {gold.get('A', 0)}/{gold.get('B', 0)}/{gold.get('C', 0)}/{gold.get('D', 0)}。题目-only 明显偏向 A/B，因此不能把它当作证据票。",
    ]
    for mask in MASKS:
        title = "严格遮蔽标签（注意 tail 实际未遮蔽）" if mask == "masked" else "未遮蔽检索范围"
        lines += ["", f"## {3 if mask == 'masked' else 4}. 去先验硬集：{title}", ""]
        table = report["hard"][mask]
        lines += ["| 方法 | 正确/硬集 | 硬集准确率 | 相对 tail 胜/负 | McNemar p |", "|---|---:|---:|---:|---:|"]
        for method in METHODS:
            item = table[method]
            if method == "tail":
                lines.append(f"| {method} | {item['correct']}/{item['total']} | {pct(item['accuracy'])} | — | — |")
            else:
                stat = item["vs_tail"]
                lines.append(f"| {method} | {item['correct']}/{item['total']} | {pct(item['accuracy'])} | {stat['wins']}/{stat['losses']} | {stat['p_raw']:.4f} |")
        lines += ["", "### 复合互补方法", "", "| 复合方法 | 正确/硬集 | 准确率 | 相对 tail 胜/负 | p |", "|---|---:|---:|---:|---:|"]
        for method, item in sorted(table.items(), key=lambda pair: -pair[1]["accuracy"]):
            if method in METHODS:
                continue
            stat = item["vs_tail"]
            lines.append(f"| {method} | {item['correct']}/{item['total']} | {pct(item['accuracy'])} | {stat['wins']}/{stat['losses']} | {stat['p_raw']:.4f} |")
        oracle = report["hard_oracle"][mask]
        lines += ["", f"硬集图谱方法 oracle：{oracle['correct']}/{oracle['total']}（{pct(oracle['accuracy'])}）。", ""]
    lines += [
        "## 5. 过程回溯：为什么丰富图谱没有稳定超过 tail",
        "",
        "1. 建图覆盖已经不是首要瓶颈：第一批金标可答题中约 97% 的答案关键词曾出现在图节点中；第二批图更大，但准确率没有随规模提高。",
        "2. 约 40% 节点孤立，且检索会混入真实但与当前选项无关的事实、早期诱饵和翻译别名，7B 容易把相关性误当因果证据。",
        "3. 单方法错误高度相关，但仍存在明显互补 oracle；说明候选集合常包含正确答案，失败发生在证据排序、选项对齐和最终选择。",
        "4. C6 第一批领先、第二批明显退化，说明基于自报置信度和候选摘要的仲裁器发生跨小说分布漂移。",
        "5. tail 的优势部分来自答案常在结局附近，当前 masked-tail 仍截取未遮蔽源小说的末尾约 5 万字符，不能用于公平的严格遮蔽比较。",
        "",
        "## 6. 单一方法突破路线",
        "",
        "### A. C4-R：证据预算受控的选项对比检索（首选）",
        "",
        "- 每个选项先生成一个可证伪命题，而不是直接检索选项原文。",
        "- 每项最多保留 2 条支持、2 条反证，强制总证据预算不超过 8 段。",
        "- 检索分数加入实体一致性、动作方向、时间位置和直接引文覆盖率；孤立节点及 decoy 边降权。",
        "- 最终回答前执行三项校验：主体是否一致、动作方向是否反转、引用是否直接蕴含选项。",
        "- 预期突破点：C4 是第二批和 20 本 unmasked 最稳定方法，只需从 45.1% 提升 3-5 题即可形成可见优势。",
        "",
        "### B. Tail-to-Graph 反向检索",
        "",
        "先用小说尾窗口形成候选假设，再到图谱中只检索能够证实或推翻该假设的跨段落证据。这样保留 tail 对结局信息的优势，同时用图解决身份、时间顺序和跨段落事实题。",
        "",
        "### C. Compress-to-Graph：压缩全局记忆 + 图谱细节",
        "",
        "先用 1–1.3 万字符的全书压缩摘要提供人物、案件主线和全局时间轴，再让图谱只补充摘要中缺失或不确定的细节与反证。旧 7 本实验中 compress 42.4% 高于 tail 35.6%，说明它不是全量原文的替代标签，而是一条有独立价值的有限上下文基线。",
        "",
        "### D. 图谱清洁而非扩图",
        "",
        "优先减少孤立节点、合并翻译别名、删除无证据 related_to 边，并为每条边保存主体—谓词—客体方向校验。目标不是更多节点，而是更高的每题有效证据密度。",
        "",
        "## 7. 复合互补突破路线",
        "",
        "### A. Tail+C4 可审计门控",
        "",
        "默认采用 tail；只有当 C4 提供直接引文、主体/方向校验通过，并且其他图方法给出可验证支持时才切换到 C4。已完成的按小说留一门控在全量 164 题上为 79/164（48.2%），高于 tail 的 43.9%；相对 tail 胜 9 负 2，McNemar p=0.0654，接近但尚未达到显著。硬集为 36/105（34.3%），只比 tail 多 2 题。",
        "",
        "Tail+C4 的事后 oracle 为全量 103/164（62.8%）、硬集 54/105（51.4%），证明互补空间很大；真正瓶颈是可靠判断分歧题，而不是缺少候选答案。固定的可审计门控达到 78/164（47.6%），可作为下一轮无需后验调参的起点。",
        "",
        "### B. 按小说留一训练的加权证据投票",
        "",
        "本报告已测试按小说留一的全局加权、题型加权和最佳方法路由。只有按小说留一结果可用于判断泛化；在全部 20 本上直接选择最优权重属于后验过拟合。",
        "",
        "### C. Disagreement-triggered verifier",
        "",
        "当 tail 与 C4 一致时直接输出；不一致时，构造一份只包含双方直接引文的盲化对照表，让独立 verifier 判断哪一方得到文本蕴含。这样把昂贵仲裁只用于真正有信息增益的分歧题。",
        "",
        "### D. Compress+Tail+Graph 三层互补",
        "",
        "压缩摘要负责全局人物与主线，tail 负责结局局部信息，图谱负责跨段证据与反证。只在三者冲突时启动 verifier，并将摘要句、尾窗口引文和图谱原文片段按来源分栏，避免压缩摘要中的推断被误当作原文事实。",
        "",
        "### E. 题目-only 作为诊断器而非候选票",
        "",
        "题目-only 的作用是识别数据先验和评估硬集，不应直接加入证据投票；在硬集中它按定义全部错误，加入投票只会污染证据决策。可以利用其预测分布检测选项偏置，但不能把其自报理由当证据。",
        "",
        "## 8. 下一轮最小可行实验",
        "",
        "1. 修复 tail 的真正 mask，形成公平 masked 基线。",
        "2. 在 105 题硬集上实现 C4-R；预注册主指标为 unmasked hard-set accuracy，相对 tail 做按题 McNemar、按小说 Bootstrap。",
        "3. 实现 Compress-to-Graph，并与 tail 使用同一输入字符预算，区分‘全局压缩覆盖’与‘末尾局部覆盖’的贡献。",
        "4. 继续验证 Tail+C4 门控，只用直接证据数、引用覆盖、主体/方向校验和方法一致性；重点处理双方不一致的题。",
        "5. 所有阈值采用 leave-one-novel-out 选择；最终同时报告 164 题全量、105 题硬集、两批独立结果。",
        "6. 成功标准：硬集相对 tail 至少 +5 个百分点，且两个 10 本批次方向一致；不再以单批最高点作为结论。",
        "",
        "## 9. 历史基线摘要",
        "",
        "- 旧图 7 本 59 题：tail 35.6%，compress 42.4%，最佳图方法 v5b masked / v7 unmasked 均 44.1%，金标 50.8%-54.2%。",
        "- 方案 C 第一批初始实验 10 本 90 题：tail 43.3%，compress 43.3%，最佳图方法约 43.3%，金标 54.4%-55.6%。",
        "- 独立改进第一批：unmasked C6 50.0%，C1/C2 47.8%，C4/tail 45.6%。",
        "- 第二批外部验证：unmasked C4 44.6%，tail 41.9%，C6 36.5%，C2 35.1%，C3-first 33.8%，C1 29.7%。",
        "- 20 本合并：unmasked C4 45.1%，tail/C6 43.9%，C2 42.1%，C1/C3-first 39.6%；题目-only 36.0%。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    rows = load()
    hard = [r for r in rows if not r["qonly_correct"]]
    q_correct = sum(r["qonly_correct"] for r in rows)
    report: dict[str, Any] = {
        "question_only": {"correct": q_correct, "total": len(rows), "accuracy": q_correct / len(rows), "hard_total": len(hard)},
        "question_only_by_batch": {},
        "question_only_by_type": by_group(rows, "question_type"),
        "question_only_by_novel": by_group(rows, "novel"),
        "full": {},
        "hard": {},
        "hard_oracle": {},
        "history": history(),
    }
    for batch in ("first10", "second10"):
        group = [r for r in rows if r["batch"] == batch]
        correct = sum(r["qonly_correct"] for r in group)
        report["question_only_by_batch"][batch] = {"correct": correct, "total": len(group), "accuracy": correct / len(group)}
    for mask in MASKS:
        full_predictions = ensemble_predictions(rows, mask)
        hard_predictions = ensemble_predictions(hard, mask)
        report["full"][mask] = evaluate_predictions(rows, full_predictions)
        report["hard"][mask] = evaluate_predictions(hard, hard_predictions)
        oracle = sum(any(is_correct(r, m, mask) for m in GRAPH_ALL) for r in hard)
        report["hard_oracle"][mask] = {"correct": oracle, "total": len(hard), "accuracy": oracle / len(hard)}
    report["qonly_prediction_distribution"] = dict(Counter(r["qonly_letter"] for r in rows))
    report["qonly_gold_distribution"] = dict(Counter(r["gold_letter"] for r in rows))
    gate_path = QONLY / "tail_c4_gate_analysis.json"
    if gate_path.exists():
        report["tail_c4_gate"] = json.loads(gate_path.read_text(encoding="utf-8"))
    QONLY.mkdir(parents=True, exist_ok=True)
    (QONLY / "analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    fields = ["batch", "novel", "qi", "qid", "question_type", "question", "choices", "gold_letter", "qonly_letter", "qonly_correct"] + [f"{m}_{mask}" for mask in MASKS for m in METHODS]
    with (QONLY / "hard_set_matrix.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in hard:
            out = {key: row[key] for key in fields}
            out["choices"] = json.dumps(row["choices"], ensure_ascii=False)
            writer.writerow(out)
    (QONLY / "REPORT.md").write_text(report_md(report), encoding="utf-8")
    print(json.dumps(report["question_only"], ensure_ascii=False))
    for mask in MASKS:
        print(mask, json.dumps(report["hard"][mask], ensure_ascii=False))


if __name__ == "__main__":
    main()
