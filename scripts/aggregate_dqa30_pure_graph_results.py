"""Aggregate the frozen 30-novel evaluation using five baseline-free graph conditions.

G1--G3 are fixed option-order probes over the same graph evidence, G4 is their
deterministic graph-only majority, and G5 is the frozen G7 tight graph expansion.
No graph condition reads a tail, compression, RAG, Q0, or gold prediction.
"""

from __future__ import annotations

import csv
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs" / "four_datasets"
OUT = ROOT / "paper" / "generated"
METHODS = ("G1", "G2", "G3", "G4", "G5", "B1", "B2", "B3", "Q0")
GRAPHS = METHODS[:5]
BASELINES = ("B1", "B2", "B3")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def majority(a: str, b: str, c: str) -> str:
    counts = Counter((a, b, c)); best = max(counts.values())
    tied = {letter for letter, count in counts.items() if count == best}
    return a if a in tied else sorted(tied)[0]


def index_json(root: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    result = {}
    for path in root.rglob("q*.json"):
        item = read_json(path)
        if item.get("qid"):
            result[item["qid"]] = (path, item)
    return result


def load_rows() -> list[dict[str, Any]]:
    old_graph = read_csv(BASE / "dqa_local_c24_pure9_consensus20" / "per_question.csv")
    old_q0 = {(row["novel"], row["qid"]): row["closed35"] for row in read_csv(BASE / "dqa_local_c16_consensus20" / "per_question.csv")}
    g7 = index_json(BASE / "dqa30_attention" / "g7_pure_graph_tight" / "answers")
    if len(g7) != 234:
        raise RuntimeError(f"G7 must contain 234 rows, found {len(g7)}")
    rows: list[dict[str, Any]] = []
    for source in old_graph:
        novel, qi, qid = source["novel"], int(source["qi"]), source["qid"]
        base_path = BASE / "dqa30_frozen_old20_baselines9b" / "answers" / novel / f"q{qi:02d}.json"
        if not base_path.is_file():
            raise FileNotFoundError(f"missing old20 baseline: {base_path}")
        base = read_json(base_path); g7_path, g7_item = g7[qid]
        selected = {
            "G1": source["original"], "G2": source["reversed"], "G3": source["cyclic"],
            "G4": majority(source["original"], source["reversed"], source["cyclic"]),
            "G5": g7_item["selected_letter"], "B1": source["tail"],
            "B2": base["answers"]["B2"]["selected_letter"], "B3": base["answers"]["B3"]["selected_letter"],
            "Q0": old_q0[(novel, qid)],
        }
        rows.append(make_row("old20", novel, qi, qid, source["gold"], selected, base["graph_sha256"], {
            "G1_G3": "dqa_local_c24_pure9_consensus20 upstream prediction caches",
            "G4": "deterministic majority(G1,G2,G3); no baseline access",
            "G5": str(g7_path.relative_to(ROOT)), "baselines": str(base_path.relative_to(ROOT)),
        }))
    new_root = BASE / "dqa30_attention" / "batch03_eval" / "answers"
    for path in sorted(new_root.glob("*/q*.json"), key=lambda p: (int(p.parent.name), p.name)):
        item = read_json(path); answers = item["answers"]; qid = item["qid"]
        g7_path, g7_item = g7[qid]
        p1, p2, p3 = (answers[key]["selected_letter"] for key in ("G1", "G2", "G3"))
        selected = {"G1": p1, "G2": p2, "G3": p3, "G4": majority(p1, p2, p3), "G5": g7_item["selected_letter"], **{key: answers[key]["selected_letter"] for key in ("B1", "B2", "B3", "Q0")}}
        rows.append(make_row("new10", str(item["novel"]), int(item["qi"]), qid, item["gold_letter"], selected, item["graph_sha256"], {
            "G1_G3_and_baselines": str(path.relative_to(ROOT)), "G4": "deterministic majority(G1,G2,G3); no baseline access", "G5": str(g7_path.relative_to(ROOT)),
        }))
    if len(rows) != 234 or len({row["qid"] for row in rows}) != 234:
        raise RuntimeError(f"expected 234 unique questions, found {len(rows)}")
    return rows


def make_row(cohort: str, novel: str, qi: int, qid: str, gold: str, selected: dict[str, str], graph_hash: str, source: dict[str, str]) -> dict[str, Any]:
    invalid = {method: value for method, value in selected.items() if value not in "ABCD"}
    if invalid:
        raise RuntimeError(f"invalid parsed answer {qid}: {invalid}")
    return {"cohort": cohort, "novel": novel, "qi": qi, "qid": qid, "gold": gold, "selected": selected, "correct": {method: selected[method] == gold for method in METHODS}, "graph_sha256": graph_hash, "source_signature": source}


def wilson(correct: int, total: int) -> list[float]:
    if not total: return [0.0, 0.0]
    z = 1.959963984540054; p = correct / total; d = 1 + z*z/total
    center = (p + z*z/(2*total))/d
    radius = z*math.sqrt(p*(1-p)/total + z*z/(4*total*total))/d
    return [max(0.0, center-radius), min(1.0, center+radius)]


def bootstrap(rows: list[dict[str, Any]], method: str, baseline: str | None = None, samples: int = 5000) -> list[float]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows: grouped[row["novel"]].append(row)
    novels = sorted(grouped, key=int); rng = random.Random(20260824); values = []
    for _ in range(samples):
        sample = [row for _ in novels for row in grouped[rng.choice(novels)]]
        value = sum(row["correct"][method] for row in sample)/len(sample)
        if baseline: value -= sum(row["correct"][baseline] for row in sample)/len(sample)
        values.append(value)
    values.sort(); return [values[int(.025*samples)], values[min(int(.975*samples), samples-1)]]


def score(rows: list[dict[str, Any]], method: str) -> dict[str, Any]:
    correct = sum(row["correct"][method] for row in rows)
    novels = sorted({row["novel"] for row in rows}, key=int)
    macro = sum(sum(row["correct"][method] for row in rows if row["novel"] == novel)/sum(row["novel"] == novel for row in rows) for novel in novels)/len(novels)
    return {"correct": correct, "total": len(rows), "micro_accuracy": correct/len(rows), "macro_novel_accuracy": macro, "wilson_95": wilson(correct, len(rows)), "novel_cluster_bootstrap_95": bootstrap(rows, method)}


def mcnemar(rows: list[dict[str, Any]], method: str, baseline: str) -> dict[str, Any]:
    wins = sum(row["correct"][method] and not row["correct"][baseline] for row in rows)
    losses = sum(not row["correct"][method] and row["correct"][baseline] for row in rows)
    n = wins + losses; p = 1.0 if not n else min(1.0, 2*sum(math.comb(n, i) for i in range(min(wins, losses)+1))/(2**n))
    return {"wins": wins, "losses": losses, "discordant": n, "exact_p": p, "delta": score(rows, method)["micro_accuracy"]-score(rows, baseline)["micro_accuracy"], "novel_cluster_delta_95": bootstrap(rows, method, baseline)}


def cohort(rows: list[dict[str, Any]]) -> dict[str, Any]:
    hard = [row for row in rows if not row["correct"]["Q0"]]
    easy = [row for row in rows if row["correct"]["Q0"]]
    pairs = {f"{g}_vs_{b}": mcnemar(rows, g, b) for g in GRAPHS for b in BASELINES}
    ordered = sorted(pairs, key=lambda key: pairs[key]["exact_p"]); running = 0.0
    for rank, key in enumerate(ordered):
        running = max(running, min(1.0, pairs[key]["exact_p"]*(len(ordered)-rank))); pairs[key]["holm_p"] = running
    return {"questions": len(rows), "novels": len({row["novel"] for row in rows}), "all": {m: score(rows, m) for m in METHODS}, "q0_wrong": {m: score(hard, m) for m in METHODS if m != "Q0"}, "q0_correct_preservation": {m: score(easy, m) for m in METHODS if m != "Q0"}, "paired_graph_vs_baselines": pairs}


def pct(value: float) -> str:
    return f"{100*value:.2f}\\%"


def write_latex(report: dict[str, Any]) -> None:
    pooled = report["descriptive30"]; best = max(GRAPHS, key=lambda m: pooled["all"][m]["micro_accuracy"])
    macros = {
        "TotalQuestions": str(pooled["questions"]), "BestGraphMethod": best,
        "BestGraphAcc": pct(pooled["all"][best]["micro_accuracy"]), "BestGraphHardAcc": pct(pooled["q0_wrong"][best]["micro_accuracy"]),
        "TailAcc": pct(pooled["all"]["B1"]["micro_accuracy"]), "CompressAcc": pct(pooled["all"]["B2"]["micro_accuracy"]),
        "RAGAcc": pct(pooled["all"]["B3"]["micro_accuracy"]), "QZeroAcc": pct(pooled["all"]["Q0"]["micro_accuracy"]),
    }
    (OUT / "results_macros.tex").write_text("\n".join(f"\\newcommand{{\\{key}}}{{{value}}}" for key, value in macros.items())+"\n", encoding="utf-8")
    labels = {"G1":"Graph order 1", "G2":"Graph order 2", "G3":"Graph order 3", "G4":"Graph-only majority", "G5":"Tight graph expansion", "B1":"Tail window", "B2":"Whole-book compression", "B3":"Vector RAG", "Q0":"Question only"}
    lines = ["\\begin{tabular}{lrrrr}", "\\toprule", "Method & Correct & All & Novel macro & Q0-hard \\\\", "\\midrule"]
    for method in METHODS:
        item = pooled["all"][method]; hard = "--" if method == "Q0" else pct(pooled["q0_wrong"][method]["micro_accuracy"])
        lines.append(f"{labels[method]} & {item['correct']}/{item['total']} & {pct(item['micro_accuracy'])} & {pct(item['macro_novel_accuracy'])} & {hard} \\\\")
    lines.extend(["\\bottomrule", "\\end{tabular}"])
    (OUT / "main_results_table.tex").write_text("\n".join(lines)+"\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True); rows = load_rows()
    report = {"metadata": {"protocol": "dqa30-frozen-pure-graph-v2", "single_answer_model": "qwen3.5:9b", "thinking": "disabled", "mask": "unmasked", "graph_methods": {"G1":"original option order", "G2":"reversed option order mapped back", "G3":"cyclic option order mapped back", "G4":"deterministic majority of G1-G3", "G5":"G7 tight graph expansion; baseline_access=false"}, "warning": "old20 and new10 use different graph-build versions; pooled30 is descriptive", "development_warning": "All graph conditions are exploratory on these 30 novels; G4 is a deterministic composite and G5 was developed on this corpus."}, "old20": cohort([r for r in rows if r["cohort"] == "old20"]), "new10": cohort([r for r in rows if r["cohort"] == "new10"]), "descriptive30": cohort(rows)}
    (OUT / "dqa30_frozen_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (OUT / "dqa30_per_question.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["cohort","novel","qi","qid","gold",*METHODS,*[f"correct_{m}" for m in METHODS],"graph_sha256","source_signature"]
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for row in rows: writer.writerow({"cohort":row["cohort"],"novel":row["novel"],"qi":row["qi"],"qid":row["qid"],"gold":row["gold"],**row["selected"],**{f"correct_{m}":row["correct"][m] for m in METHODS},"graph_sha256":row["graph_sha256"],"source_signature":json.dumps(row["source_signature"],ensure_ascii=False)})
    with (OUT / "dqa30_method_summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields=["cohort","subset","method","correct","total","micro_accuracy","macro_novel_accuracy","ci_low","ci_high"]; writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
        for name in ("old20","new10","descriptive30"):
            for subset in ("all","q0_wrong","q0_correct_preservation"):
                for method,item in report[name][subset].items(): writer.writerow({"cohort":name,"subset":subset,"method":method,"correct":item["correct"],"total":item["total"],"micro_accuracy":item["micro_accuracy"],"macro_novel_accuracy":item["macro_novel_accuracy"],"ci_low":item["wilson_95"][0],"ci_high":item["wilson_95"][1]})
    write_latex(report)
    print(json.dumps({name:{m:block["micro_accuracy"] for m,block in report[name]["all"].items()} for name in ("old20","new10","descriptive30")},ensure_ascii=False,indent=2))


if __name__ == "__main__": main()
