"""Graph-only disagreement referee for frozen G7 and G9 predictions."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"scripts")); sys.path.insert(0,str(ROOT/"src"))
from c8_graph_passage import LETTERS, normalize_letter
from native_ollama_client import NativeOllamaNoThinkClient
from run_dqa_g7_pure_graph import complete_choice

def combine(a,b):
    chunks={}
    for source,item in (("G7",a),("G9",b)):
        for row in item["retrieval"]["chunks"]:
            entry=chunks.setdefault(row["id"],dict(row)); entry.setdefault("schedulers",[]).append(source)
    ordered=sorted(chunks.values(),key=lambda r:int(r["start"]))
    links=[]; seen=set()
    for item in (a,b):
        for row in item["retrieval"].get("links",[]):
            key=(row.get("source"),row.get("relation"),row.get("target"),row.get("chunk_id"))
            if key not in seen: seen.add(key); links.append(row)
    return ordered,links

def referee_prompt(a,b,chunks,links):
    options="\n".join(f"{LETTERS[i]}. {v}" for i,v in enumerate(b["choices"][:4]))
    passages="\n\n".join(f"[{r['id']} | schedulers={','.join(r.get('schedulers',[]))}]\n{r['text']}" for r in chunks)
    graph="\n".join(f"- {r.get('source','')} --{r.get('relation','')}--> {r.get('target','')} [{r.get('chunk_id','')}: {r.get('evidence','')[:240]}]" for r in links)
    return f"QUESTION\n{b['question']}\n\nOPTIONS\n{options}\n\nGRAPH SCHEDULER PROPOSALS\nG7 proposed {a['selected_letter']}; G9 proposed {b['selected_letter']}. These are hypotheses, not votes.\n\nUNION OF GRAPH-SELECTED ORIGINAL PASSAGES\n{passages}\n\nGROUNDED GRAPH LINKS\n{graph or '[none]'}\n\nRe-evaluate all four options from the quoted novel text. Resolve the disagreement using explicit final revelations and causal facts, not scheduler agreement. Treat absence as unknown, reject early suspicion contradicted later, and handle NOT/EXCEPT literally. Return JSON only: {{\"selected_letter\":\"A|B|C|D\",\"reason\":\"brief direct comparison\"}}"

def main():
    p=argparse.ArgumentParser(); p.add_argument("--g7",type=Path,required=True); p.add_argument("--g9",type=Path,required=True); p.add_argument("--out",type=Path,required=True); p.add_argument("--model",default="qwen3.5:9b"); p.add_argument("--num-ctx",type=int,default=16384); args=p.parse_args(); args.out.mkdir(parents=True,exist_ok=True); client=NativeOllamaNoThinkClient(args.model,num_ctx=args.num_ctx); rows=[]
    for path in sorted(args.g9.rglob("q*.json"),key=lambda p:(int(p.parent.name),p.name)):
        b=json.loads(path.read_text(encoding="utf-8")); a=json.loads((args.g7/path.parent.name/path.name).read_text(encoding="utf-8")); out=args.out/"answers"/path.parent.name/path.name
        if a["selected_letter"]==b["selected_letter"]: selected=a["selected_letter"]; raw={"route":"agreement"}
        else:
            chunks,links=combine(a,b); raw=complete_choice(client,"You are a conservative graph-evidence referee. Do not use outside knowledge.",referee_prompt(a,b,chunks,links),max_tokens=360); selected=normalize_letter(raw.get("selected_letter")) or a["selected_letter"]
        row={"version":"g10-graph-only-disagreement-referee-v1","novel":b["novel"],"qi":b["qi"],"qid":b["qid"],"question":b["question"],"choices":b["choices"],"gold_letter":b["gold_letter"],"g7":a["selected_letter"],"g9":b["selected_letter"],"selected_letter":selected,"correct":selected==b["gold_letter"],"disagreement":a["selected_letter"]!=b["selected_letter"],"raw":raw,"baseline_access":False,"gold_access":False}; out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(row,ensure_ascii=False,indent=1),encoding="utf-8"); rows.append(row); print(f"{row['novel']}/q{row['qi']} G7={row['g7']} G9={row['g9']} G10={selected} gold={row['gold_letter']}",flush=True)
    summary={"correct":sum(r["correct"] for r in rows),"total":len(rows),"accuracy":sum(r["correct"] for r in rows)/len(rows),"disagreements":sum(r["disagreement"] for r in rows)}; (args.out/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8"); print(json.dumps(summary,indent=2))
if __name__=="__main__": main()
