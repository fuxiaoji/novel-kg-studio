"""Generate all LaTeX tables + prose macros for the NeurIPS 2026 draft.

Reads the verified aggregation JSONs (paper/generated/) and writes descriptive-name
tables into paper/neurips2026/tables/. NO method codes (G7/G9/G10/B1/B2/B3/Q0)
may appear in any generated .tex -- see the name mapping below.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "paper" / "generated"
OUT = ROOT / "paper" / "neurips2026" / "tables"

# ---------------------------------------------------------------------------
# Descriptive names (internal code -> paper name). THE canonical mapping.
# ---------------------------------------------------------------------------
NAMES = {
    "B1": "Recent-window baseline",
    "B2": "Whole-book compression baseline",
    "B3": "Vector RAG baseline",
    "Q0": "Question-only control",
    "Q0T": "Question-only control (terse)",
    "GOLD_ORIG": "Options-first gold oracle",
    "GOLD_V1": "Gold oracle (evidence-first, original order)",
    "GOLD_V2": "Fair gold oracle (gold ceiling)",
    "GOLD_V3": "Gold oracle (options-first, shuffled)",
    "G7": "Graph-guided evidence expansion",
    "G9": "Graph-native chunk reranking",
    "G10": "Graph-based disagreement arbitration",
}
# Master-table order (fair gold first, then graphs, then baselines, then control)
MAIN_ORDER = ["GOLD_V2", "G7", "G9", "G10", "B1", "B2", "B3", "Q0"]
GOLD_DIST = {"A": 58, "B": 53, "C": 58, "D": 65}  # content-implied gold distribution
GOLD_ORIG_ANSWERS = ROOT / "outputs" / "four_datasets" / "dqa30_attention" / "goldonly_9b_30" / "answers"


def pct(x: float) -> str:
    return f"{100 * x:.1f}"


def esc(s: str) -> str:
    return s.replace("&", "\\&").replace("%", "\\%")


def load() -> tuple[dict, dict]:
    fair = json.loads((GEN / "dqa30_fair_gold_results.json").read_text(encoding="utf-8"))
    latest = json.loads((GEN / "dqa30_latest30_results.json").read_text(encoding="utf-8"))
    return fair, latest


def fair_gold_q0hard(fair_answers_dir: Path, frozen_csv: Path) -> tuple[int, int]:
    """Fair-gold accuracy on the Q0-hard subset (questions the question-only control gets wrong)."""
    ans = {}
    for p in fair_answers_dir.rglob("q*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("qid"):
            ans[d["qid"]] = d.get("correct")
    n = k = 0
    for row in csv.DictReader(frozen_csv.open(encoding="utf-8-sig", newline="")):
        if row["Q0"] == row["gold"]:
            continue
        n += 1
        if ans.get(row["qid"]):
            k += 1
    return k, n


def write_macros(fair: dict, latest: dict) -> None:
    pooled = fair["pooled"]
    q0h = latest["descriptive30"]["q0_wrong"]
    lines = [
        r"\newcommand{\GoldCeilingAcc}{" + pct(pooled["GOLD_V2"]["micro"]) + "}",
        r"\newcommand{\GraphExpansionAcc}{" + pct(pooled["G7"]["micro"]) + "}",
        r"\newcommand{\GoldVsGraphP}{0.912}",
        r"\newcommand{\GoldVsGraphDelta}{+" + f"{100*(pooled['GOLD_V2']['micro']-pooled['G7']['micro']):.2f}" + r"\pp}",
        r"\newcommand{\GoldVsTerseDelta}{+" + f"{100*fair['paired']['GOLD_V2_vs_Q0T']['delta']:.2f}" + r"\pp}",
        r"\newcommand{\GoldVsTerseP}{0.0005}",
        r"\newcommand{\GraphExpansionHard}{" + pct(q0h["G7"]["micro_accuracy"]) + "}",
        r"\newcommand{\GraphRerankingHard}{" + pct(q0h["G9"]["micro_accuracy"]) + "}",
        r"\newcommand{\GraphArbitrationHard}{" + pct(q0h["G10"]["micro_accuracy"]) + "}",
        r"\newcommand{\TailAcc}{" + pct(pooled["B1"]["micro"]) + "}",
        r"\newcommand{\CompressAcc}{" + pct(pooled["B2"]["micro"]) + "}",
        r"\newcommand{\RAGAcc}{" + pct(pooled["B3"]["micro"]) + "}",
        r"\newcommand{\QZeroAcc}{" + pct(pooled["Q0"]["micro"]) + "}",
        r"\newcommand{\GoldOrigAcc}{" + pct(pooled["GOLD_ORIG"]["micro"]) + "}",
        r"\newcommand{\GoldOldTwenty}{" + pct(fair["old20"]["GOLD_V2"]["micro"]) + "}",
        r"\newcommand{\GoldNewTen}{" + pct(fair["new10"]["GOLD_V2"]["micro"]) + "}",
        r"\newcommand{\GraphExpansionOldTwenty}{" + pct(fair["old20"]["G7"]["micro"]) + "}",
        r"\newcommand{\GraphExpansionNewTen}{" + pct(fair["new10"]["G7"]["micro"]) + "}",
        r"\newcommand{\GraphArbitrationNewTen}{" + pct(fair["new10"]["G10"]["micro"]) + "}",
        r"\newcommand{\TotalQuestions}{234}",
        r"\newcommand{\QuestionCountOldTwenty}{164}",
        r"\newcommand{\QuestionCountNewTen}{70}",
    ]
    (OUT / "macros.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_main_results(fair: dict, latest: dict, q0h_fair: tuple[int, int]) -> None:
    rows = []
    for m in MAIN_ORDER:
        a = fair["old20"][m]["micro"]
        b = fair["new10"][m]["micro"]
        c = fair["pooled"][m]["micro"]
        name = NAMES[m]
        # Q0-hard values
        if m in ("Q0",):
            hard = "--"
        elif m in ("G7", "G9", "G10"):
            hard = pct(latest["descriptive30"]["q0_wrong"][m]["micro_accuracy"])
        elif m == "GOLD_V2":
            hard = f"{pct(q0h_fair[0]/q0h_fair[1])} ({q0h_fair[0]}/{q0h_fair[1]})"
        else:
            hard = pct(latest["descriptive30"]["q0_wrong"].get(m, {}).get("micro_accuracy", 0) or 0)
        if m == "GOLD_V2":
            row = (f"\\textbf{{{esc(name)}}} & {pct(a)} & {pct(b)} & \\textbf{{{pct(c)}}}\\% & {hard} \\\\")
        elif m in ("G7", "G9", "G10"):
            row = f"{esc(name)} & {pct(a)} & {pct(b)} & {pct(c)}\\% & {hard} \\\\"
        else:
            row = f"{esc(name)} & {pct(a)} & {pct(b)} & {pct(c)}\\% & {hard} \\\\"
        rows.append(row)

    body = "\n".join(
        [
            "\\begin{tabular}{lcccc}",
            "\\toprule",
            "Method & old20 & new10 & Pooled & Hard subset \\\\",
            "\\midrule",
            rows[0],
            "\\midrule",
            *rows[1:4],
            "\\midrule",
            *rows[4:7],
            "\\midrule",
            rows[7],
            "\\bottomrule",
            "\\end{tabular}",
        ]
    )
    note = (
        "Pooled values are descriptive: the first 20 and final 10 graphs come from different "
        "frozen pipelines. The fair gold oracle is the de-biased upper bound "
        "(evidence before options, per-question shuffled options; Section~\\ref{sec:fairgold}). "
        "The hard subset is the 140 questions the question-only control answers incorrectly (the ones that actually require evidence)."
    )
    (OUT / "main_results.tex").write_text(body + "\n" + f"\\vspace{{2pt}}\\begin{{scriptsize}}{note}\\end{{scriptsize}}\n", encoding="utf-8")


def load_selected_over_234(path: Path) -> dict[str, int]:
    """Count selected letters over the full 234-question set (no-selection => not counted)."""
    counts = {k: 0 for k in "ABCD"}
    for p in path.rglob("q*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        letter = d.get("selected_letter")
        if isinstance(letter, str) and letter in "ABCD":
            counts[letter] += 1
    return counts


def write_fair_gold_analysis(fair: dict) -> None:
    per = fair["per_gold_letter"]
    sel = fair["selected_letter_dist"]
    labels = {"GOLD_V2": "Fair gold oracle", "Q0T": "Question-only (terse)", "GOLD_ORIG": "Options-first gold oracle"}
    order = ["GOLD_V2", "Q0T", "GOLD_ORIG"]
    n_total = 234

    # Panel 1: per-letter accuracy
    rows1 = []
    for m in order:
        cells = []
        for k in "ABCD":
            cell = f"{pct(per[m][k]['acc'])}\\%"
            if m == "GOLD_ORIG" and k == "D":
                cell = f"\\textbf{{{pct(per[m][k]['acc'])}}}\\%"
            cells.append(cell)
        rows1.append(f"{labels[m]} & {' & '.join(cells)} \\\\")
    panel1 = "\n".join(
        [
            "\\begin{subtable}{\\linewidth}",
            "\\centering",
            "\\caption{Per-gold-letter accuracy}",
            "\\small",
            "\\begin{tabular}{lcccc}",
            "\\toprule",
            "Run & A & B & C & D \\\\",
            "\\midrule",
            *rows1,
            "\\bottomrule",
            "\\end{tabular}",
            "\\end{subtable}",
        ]
    )

    # Panel 2: selected-letter distribution (shares of all 234 questions) vs gold distribution
    gvals = " & ".join(f"{GOLD_DIST[k]} ({pct(GOLD_DIST[k]/n_total)}\\%$^{{\\dagger}}$)" for k in "ABCD")
    rows2 = []
    for m in order:
        d = load_selected_over_234(GOLD_ORIG_ANSWERS) if m == "GOLD_ORIG" else sel[m]
        vals = " & ".join(f"{d[k]} ({pct(d[k]/n_total)}\\%$^{{\\dagger}}$)" for k in "ABCD")
        rows2.append(f"{labels[m]} & {vals} \\\\")
    rows2.append(f"Gold-letter distribution & {gvals} \\\\")
    panel2 = "\n".join(
        [
            "\\begin{subtable}{\\linewidth}",
            "\\centering",
            "\\caption{Selected-letter distribution}",
            "\\small",
            "\\begin{tabular}{lcccc}",
            "\\toprule",
            "Run & A & B & C & D \\\\",
            "\\midrule",
            *rows2,
            "\\bottomrule",
            "\\end{tabular}",
            "\\end{subtable}",
        ]
    )
    note = (" $^{\\dagger}$Column shares are percent of the 234 questions. The options-first gold oracle piles onto "
            "D (138/234 = 59\\% of questions) regardless of content, while the de-biased fair gold oracle matches the "
            "content-implied gold distribution. The options-first oracle refused to select a letter on 8 questions "
            "(its row sums to 226).")
    (OUT / "fair_gold_analysis.tex").write_text(
        f"{panel1}\n\\vspace{{6pt}}\n{panel2}\n\\vspace{{2pt}}\\begin{{scriptsize}}\n{note}\n\\end{{scriptsize}}\n", encoding="utf-8")


def write_paired_tests(fair: dict, latest: dict) -> None:
    def fmt_p(p: float) -> str:
        if p < 0.001:
            return "\\textbf{<0.001}" if p < 0.0005 else f"\\textbf{{{p:.4f}}}"
        return f"{p:.3f}"

    def row(label: str, delta: float, w: int, l: int, p: float, ci: list[float], holm: float | None) -> str:
        ci_s = f"[{pct(ci[0])}, {pct(ci[1])}]"
        holm_s = f"{holm:.3f}" if holm is not None else "--"
        return (f"{label} & {100*delta:+.2f}pp & {w}/{l} & {fmt_p(p)} & {holm_s} & {ci_s} \\\\")

    pairs = fair["paired"]
    p_desc = latest["descriptive30"]["paired_graph_vs_baselines"]
    p_new = latest["new10"]["paired_graph_vs_baselines"]
    lines = [
        "% L = ragged-right wrapping label column; R = wrapping CI column.",
        "\\newcolumntype{L}{>{\\raggedright\\arraybackslash}X}",
        "\\newcolumntype{R}{>{\\raggedright\\arraybackslash}p{2.2cm}}",
        "\\small",
        "\\begin{tabularx}{\\linewidth}{L rrrrR}",
        "\\toprule",
        "Paired contrast & $\\Delta$ & W/L & exact $p$ & Holm $p$ & bootstrap 95\\% CI \\\\",
        "\\midrule",
        row("Fair gold vs. question-only (terse)", pairs["GOLD_V2_vs_Q0T"]["delta"],
            pairs["GOLD_V2_vs_Q0T"]["wins"], pairs["GOLD_V2_vs_Q0T"]["losses"],
            pairs["GOLD_V2_vs_Q0T"]["exact_p"], pairs["GOLD_V2_vs_Q0T"]["novel_cluster_delta_95"], None),
        row("Fair gold vs. graph-guided expansion", pairs["GOLD_V2_vs_G7"]["delta"],
            pairs["GOLD_V2_vs_G7"]["wins"], pairs["GOLD_V2_vs_G7"]["losses"],
            pairs["GOLD_V2_vs_G7"]["exact_p"], pairs["GOLD_V2_vs_G7"]["novel_cluster_delta_95"], None),
        row("Fair gold vs. graph arbitration", pairs["GOLD_V2_vs_G10"]["delta"],
            pairs["GOLD_V2_vs_G10"]["wins"], pairs["GOLD_V2_vs_G10"]["losses"],
            pairs["GOLD_V2_vs_G10"]["exact_p"], pairs["GOLD_V2_vs_G10"]["novel_cluster_delta_95"], None),
        row("Options-first gold vs. question-only (artifact)", pairs["GOLD_ORIG_vs_Q0"]["delta"],
            pairs["GOLD_ORIG_vs_Q0"]["wins"], pairs["GOLD_ORIG_vs_Q0"]["losses"],
            pairs["GOLD_ORIG_vs_Q0"]["exact_p"], pairs["GOLD_ORIG_vs_Q0"]["novel_cluster_delta_95"], None),
        "\\midrule",
        row("Graph-guided expansion vs. recent-window (pooled)", p_desc["G7_vs_B1"]["delta"],
            p_desc["G7_vs_B1"]["wins"], p_desc["G7_vs_B1"]["losses"],
            p_desc["G7_vs_B1"]["exact_p"], p_desc["G7_vs_B1"]["novel_cluster_delta_95"], p_desc["G7_vs_B1"]["holm_p"]),
        row("Graph arbitration vs. recent-window (new10)", p_new["G10_vs_B1"]["delta"],
            p_new["G10_vs_B1"]["wins"], p_new["G10_vs_B1"]["losses"],
            p_new["G10_vs_B1"]["exact_p"], p_new["G10_vs_B1"]["novel_cluster_delta_95"], p_new["G10_vs_B1"]["holm_p"]),
        "\\bottomrule",
        "\\end{tabularx}",
    ]
    note = "Exact two-sided paired McNemar over the same 234 questions; \\textit{W/L} = wins/losses on discordant pairs. Bootstrap deltas are novel-clustered (5,000 resamples of whole novels)."
    (OUT / "paired_tests.tex").write_text("\n".join(lines) + "\n" + f"\\vspace{{2pt}}\\begin{{scriptsize}}{note}\\end{{scriptsize}}\n", encoding="utf-8")


def write_appendix_tables(fair: dict, latest: dict) -> None:
    blocks = []
    # Full cohort master table (no Q0-hard, all 12 runs)
    for cohort, block in (("old20", fair["old20"]), ("new10", fair["new10"]), ("pooled", fair["pooled"])):
        rows = []
        for m in ["GOLD_V2", "GOLD_V1", "GOLD_V3", "GOLD_ORIG", "G7", "G9", "G10", "B1", "B2", "B3", "Q0T", "Q0"]:
            d = block[m]
            rows.append(f"{NAMES[m]} & {d['correct']}/{d['n']} & {pct(d['micro'])}\\% \\\\")
        tab = "\n".join(
            [
                "\\begin{tabular}{lcc}",
                "\\toprule",
                f"Method & correct/total & micro acc. ({cohort}) \\\\",
                "\\midrule",
                *rows,
                "\\bottomrule",
                "\\end{tabular}",
            ]
        )
        blocks.append(tab)
    # Q0-correct preservation
    pres = latest["descriptive30"]["q0_correct_preservation"]
    rows = []
    for m in ["G7", "G9", "G10", "B1", "B2", "B3", "GOLD"]:
        if m not in pres:
            continue
        name = NAMES.get(m, NAMES["GOLD_ORIG"])  # "GOLD" -> options-first gold oracle
        rows.append(f"{name} & {pres[m]['micro_accuracy']*100:.1f}\\% \\\\")
    blocks.append("\n".join(["\\begin{tabular}{lc}", "\\toprule", "Method & preservation on control-correct (pooled) \\\\", "\\midrule", *rows, "\\bottomrule", "\\end{tabular}"]))
    (OUT / "appendix_full_tables.tex").write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fair, latest = load()
    q0h_fair = fair_gold_q0hard(ROOT / "outputs" / "four_datasets" / "dqa30_attention" / "goldonly_9b_30_fair" / "v2_evid_first_shuf" / "answers",
                                GEN / "dqa30_per_question.csv")
    write_macros(fair, latest)
    write_main_results(fair, latest, q0h_fair)
    write_fair_gold_analysis(fair)
    write_paired_tests(fair, latest)
    write_appendix_tables(fair, latest)
    print(f"fair-gold Q0-hard: {q0h_fair[0]}/{q0h_fair[1]} = {q0h_fair[0]/q0h_fair[1]*100:.1f}%")
    print("tables written to", OUT)


if __name__ == "__main__":
    main()
