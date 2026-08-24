"""Create exploratory novel-level relationship tables and figures."""
from __future__ import annotations
import csv,json,sys
from collections import defaultdict
from pathlib import Path
import matplotlib.pyplot as plt
from scipy.stats import pearsonr,spearmanr
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/"scripts"),str(ROOT/"src")]
from build_c_next10_graphs import merged_cases  # noqa:E402
def association(rows,x,y):
    a=[float(r[x]) for r in rows];b=[float(r[y]) for r in rows];p=pearsonr(a,b);s=spearmanr(a,b)
    return {"n":len(rows),"pearson_r":float(p.statistic),"pearson_p":float(p.pvalue),"spearman_rho":float(s.statistic),"spearman_p":float(s.pvalue)}
def main():
    out=ROOT/"paper"/"generated"
    with (out/"dqa30_per_question.csv").open(encoding="utf-8-sig",newline="") as h:q=list(csv.DictReader(h))
    with (out/"dqa30_gold_dense_regions.csv").open(encoding="utf-8-sig",newline="") as h:dense={r["novel"]:r for r in csv.DictReader(h)}
    graph={r["novel"]:r for r in json.loads((ROOT/"config"/"dqa30_frozen_graphs.json").read_text(encoding="utf-8"))["records"]};cases=merged_cases(sorted(graph,key=int));groups=defaultdict(list)
    for r in q:groups[r["novel"]].append(r)
    rows=[]
    for novel in sorted(groups,key=int):
        subset=groups[novel];n=len(subset);acc={m:sum(r[f"correct_{m}"].lower()=="true" for r in subset)/n for m in ("G5","B1","B2","B3","Q0")};d=dense[novel];g=graph[novel]
        rows.append({"novel":novel,"cohort":g["cohort"],"questions":n,"novel_characters":len(cases[novel]["text"]),"nodes":g["nodes"],"edges":g["valid_edges"],"isolate_rate":g["isolate_rate_recomputed"],"gold_position_recall":int(d["gold_positions_covered"])/int(d["gold_positions"]) if int(d["gold_positions"]) else 0.0,"core2_gold_rate":int(d["gold_core2"])/int(d["gold_nodes"]) if int(d["gold_nodes"]) else 0.0,"G5_accuracy":acc["G5"],"tail_accuracy":acc["B1"],"compression_accuracy":acc["B2"],"rag_accuracy":acc["B3"],"Q0_accuracy":acc["Q0"],"G5_minus_tail":acc["G5"]-acc["B1"],"G5_minus_best_baseline":acc["G5"]-max(acc["B1"],acc["B2"],acc["B3"])})
    tests=(("gold_position_recall","G5_accuracy"),("gold_position_recall","G5_minus_tail"),("core2_gold_rate","G5_accuracy"),("isolate_rate","G5_accuracy"),("novel_characters","G5_accuracy"));sets=(("old20",[r for r in rows if r["cohort"]=="old20"]),("new10",[r for r in rows if r["cohort"]=="new10"]),("descriptive30",rows));correlations={name:{f"{x}_vs_{y}":association(group,x,y) for x,y in tests} for name,group in sets}
    with (out/"dqa30_novel_relationships.csv").open("w",encoding="utf-8-sig",newline="") as h:w=csv.DictWriter(h,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    (out/"dqa30_relationship_correlations.json").write_text(json.dumps({"warning":"exploratory novel-level associations; no causal interpretation","correlations":correlations},ensure_ascii=False,indent=2),encoding="utf-8")
    fig,axes=plt.subplots(1,3,figsize=(11.4,3.7),constrained_layout=True);panels=(("gold_position_recall","G5_minus_tail","Gold-paragraph mapping recall","G5 - tail accuracy"),("isolate_rate","G5_accuracy","Graph isolate rate","G5 accuracy"),("novel_characters","G5_accuracy","Novel length (characters)","G5 accuracy"))
    for ax,(x,y,xlabel,ylabel) in zip(axes,panels):
        for cohort,color,marker in (("old20","#597ca6","o"),("new10","#df7b52","s")):
            group=[r for r in rows if r["cohort"]==cohort];ax.scatter([r[x] for r in group],[r[y] for r in group],s=35,c=color,marker=marker,alpha=.85,label=cohort)
        if y.startswith("G5_minus"):ax.axhline(0,color="#9aa4ae",lw=.7,ls="--")
        ax.set_xlabel(xlabel);ax.set_ylabel(ylabel);ax.grid(color="#e2e7ec",lw=.6);ax.spines[["top","right"]].set_visible(False)
    axes[0].legend(frameon=False);fig.suptitle("Exploratory novel-level associations",fontweight="bold")
    for ext in ("png","pdf","svg"):fig.savefig(out/f"dqa30_relationships.{ext}",dpi=340 if ext=="png" else None,bbox_inches="tight")
    plt.close(fig);print(json.dumps(correlations["descriptive30"],ensure_ascii=False,indent=2))
if __name__=="__main__":main()
