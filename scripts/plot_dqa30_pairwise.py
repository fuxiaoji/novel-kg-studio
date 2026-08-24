"""Plot paired accuracy deltas and question-level wins/losses."""
from __future__ import annotations
import json
from pathlib import Path
import matplotlib.pyplot as plt
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/"paper"/"generated"
def main():
    report=json.loads((OUT/"dqa30_frozen_results.json").read_text(encoding="utf-8"));best=max(("G1","G2","G3","G4","G5"),key=lambda m:report["descriptive30"]["all"][m]["micro_accuracy"])
    entries=[]
    labels={"old20":"Old 20","new10":"New 10","descriptive30":"All 30\n(descriptive)","B1":"tail","B2":"compression","B3":"RAG"}
    for cohort in ("old20","new10","descriptive30"):
        for baseline in ("B1","B2","B3"):
            item=report[cohort]["paired_graph_vs_baselines"][f"{best}_vs_{baseline}"];entries.append((f"{labels[cohort]} vs {labels[baseline]}",item))
    fig,ax=plt.subplots(figsize=(8.0,5.0),constrained_layout=True);ys=list(range(len(entries)))[::-1]
    for y,(label,item) in zip(ys,entries):
        low,high=item["novel_cluster_delta_95"];delta=item["delta"];color="#3274a1" if delta>=0 else "#c65f5f"
        ax.errorbar(delta,y,xerr=[[delta-low],[high-delta]],fmt="o",color=color,capsize=3,lw=1.2)
        ax.text(max(high,delta)+.012,y,f"W{item['wins']} / L{item['losses']}  Holm p={item['holm_p']:.3f}",va="center",fontsize=7.7,color="#4f5964")
    ax.axvline(0,color="#7c8792",lw=.8,ls="--");ax.set_yticks(ys,[label for label,_ in entries]);ax.set_xlabel(f"Accuracy difference: {best} minus baseline");ax.set_title("Paired graph--baseline differences",loc="left",fontweight="bold");ax.grid(axis="x",color="#e0e5ea",lw=.6);ax.spines[["top","right"]].set_visible(False)
    for ext in ("png","pdf","svg"):fig.savefig(OUT/f"dqa30_pairwise.{ext}",dpi=340 if ext=="png" else None,bbox_inches="tight")
    plt.close(fig)
if __name__=="__main__":main()
