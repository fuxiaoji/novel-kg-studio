"""Render the final Chinese numerical report from generated JSON artifacts."""
from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];G=ROOT/"paper"/"generated"
def pct(x):return f"{100*x:.2f}%"
def main():
    r=json.loads((G/"dqa30_frozen_results.json").read_text(encoding="utf-8"));d=json.loads((G/"dqa30_gold_dense_regions.json").read_text(encoding="utf-8"));methods=("G1","G2","G3","G4","G5","B1","B2","B3","Q0");labels={"G1":"图谱顺序1","G2":"图谱顺序2","G3":"图谱顺序3","G4":"纯图谱多数票","G5":"紧约束图扩展","B1":"尾窗口","B2":"全文普通压缩","B3":"普通向量RAG","Q0":"题目+选项"};best=max(methods[:5],key=lambda m:r["descriptive30"]["all"][m]["micro_accuracy"])
    lines=["# 30 本冻结图谱评测：最终数值报告","",f"主结论：在不读取任何基线预测的五个图谱条件中，{labels[best]}（{best}）取得最高描述性汇总准确率 {pct(r['descriptive30']['all'][best]['micro_accuracy'])}。尾窗口、普通压缩和普通 RAG 分别为 {pct(r['descriptive30']['all']['B1']['micro_accuracy'])}、{pct(r['descriptive30']['all']['B2']['micro_accuracy'])}、{pct(r['descriptive30']['all']['B3']['micro_accuracy'])}。由于前20与后10采用不同建图版本，30本合并值不能替代分层结论。","","## 方法准确率","","|方法|前20|后10|30本描述汇总|小说宏平均|Q0-hard|","|---|---:|---:|---:|---:|---:|"]
    for m in methods:
        hard="--" if m=="Q0" else pct(r["descriptive30"]["q0_wrong"][m]["micro_accuracy"])
        lines.append(f"|{labels[m]} ({m})|{pct(r['old20']['all'][m]['micro_accuracy'])}|{pct(r['new10']['all'][m]['micro_accuracy'])}|{pct(r['descriptive30']['all'][m]['micro_accuracy'])}|{pct(r['descriptive30']['all'][m]['macro_novel_accuracy'])}|{hard}|")
    lines += ["","## 最佳纯图谱方法与三基线的配对比较","","|分层|基线|增益|独有答对/独有答错|McNemar p|Holm p|小说聚类95%区间|","|---|---|---:|---:|---:|---:|---:|"]
    for cohort in ("old20","new10","descriptive30"):
        for b in ("B1","B2","B3"):
            x=r[cohort]["paired_graph_vs_baselines"][f"{best}_vs_{b}"];lo,hi=x["novel_cluster_delta_95"]
            lines.append(f"|{cohort}|{labels[b]}|{pct(x['delta'])}|{x['wins']}/{x['losses']}|{x['exact_p']:.4f}|{x['holm_p']:.4f}|[{pct(lo)}, {pct(hi)}]|")
    lines += ["","## Q0 与长文本增益","",f"Q0 在全部 234 题上的准确率为 {pct(r['descriptive30']['all']['Q0']['micro_accuracy'])}；Q0 答错的题构成 Q0-hard。{labels[best]} 在 Q0-hard 上为 {pct(r['descriptive30']['q0_wrong'][best]['micro_accuracy'])}。该指标用于减少常识可答题和潜在训练记忆的影响，但不能单独证明完全没有数据污染。","","## 金标稠密区","",f"旧20的金标节点 2-core 比例为 {pct(d['summary']['old20']['core2']['gold_rate_micro'])}，富集倍数 {d['summary']['old20']['core2']['enrichment_ratio']:.2f}；后10分别为 {pct(d['summary']['new10']['core2']['gold_rate_micro'])} 和 {d['summary']['new10']['core2']['enrichment_ratio']:.2f}。30本描述汇总的富集倍数为 {d['summary']['descriptive30']['core2']['enrichment_ratio']:.2f}。这说明金标证据在拓扑核心中更集中，但不构成注意力或答题提升的因果证据。","","## 严谨性结论","","- 主表排除了会回退到尾窗口的历史 G4/G5 混合路由；它们只保留在历史探索档案中。","- 当前五个图谱条件均不访问尾窗口、压缩、RAG、Q0 或金标预测。","- G4 是 G1--G3 的确定性纯图谱多数票，G5 的 234 条记录均声明 `baseline_access=false`。","- G4/G5 在本语料上有开发痕迹，因此结果是探索性证据，不包装为完全独立测试集。","- 每题都保留在分母中；逐题答案、证据、解析状态、估算 token、耗时可用性和运行签名见 `paper/generated/dqa30_answer_records.jsonl`。"]
    (ROOT/"paper"/"最终结果报告.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
if __name__=="__main__":main()
