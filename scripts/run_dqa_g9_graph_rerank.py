"""G9: graph-native high-recall pool, metadata rerank, and compact source QA."""
from __future__ import annotations
import argparse, hashlib, json, re, sys, time
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts")); sys.path.insert(0, str(ROOT / "src"))
import audit_dqa30_graph_native_recall as native
from analyze_dense_retrieval import chunk_embeddings, embed
from build_c_next10_graphs import merged_cases
from c8_graph_passage import C8Context, LETTERS, normalize_letter
from native_ollama_client import NativeOllamaNoThinkClient
from novel_kg_studio.llm import extract_json
from run_dqa30_g6_graph_expansion import answer_prompt, build_dossier
from run_dqa_g7_pure_graph import complete_choice

VERSION = "g9-graph-native-metadata-rerank-v1"

def parse_ids(raw):
    try: data = extract_json(raw)
    except ValueError: data = {}
    values = data.get("selected_chunk_ids", []) if isinstance(data, dict) else []
    if isinstance(values, str): values = re.findall(r"c_\d+", values)
    return [str(v) for v in values if re.fullmatch(r"c_\d+", str(v))], data

def graph_pool(ctx, docs, maps, matrix, option_vectors, question, frozen):
    resolution = embed([f"Final revelation, true solution, causal conclusion, confession, or decisive evidence answering: {question['question']} Options: {' ; '.join(question['choices'][:4])}"])
    vectors = np.vstack([option_vectors, resolution]); scores = np.max(matrix @ vectors.T, axis=1)
    by_chunk = {}
    for di in np.argsort(scores)[::-1]:
        for ci in maps[int(di)]: by_chunk.setdefault(int(ci), []).append((float(scores[int(di)]), int(di)))
    ranked = sorted(by_chunk, key=lambda ci: by_chunk[ci][0][0] + (0.15*by_chunk[ci][1][0] if len(by_chunk[ci])>1 else 0), reverse=True)
    frozen_ids = [int(row["index"]) for row in frozen["chunks"]]
    selected = native.unique(frozen_ids + ranked)[:28]
    return selected, by_chunk

def cards(ctx, docs, selected, by_chunk, frozen_ids):
    result=[]; total=max(len(ctx.base.novel_text),1)
    for ci in selected:
        facts=[]
        for score, di in by_chunk.get(ci, [])[:2]: facts.append(f"score={score:.3f} {docs[di][:360]}")
        result.append(f"[{ctx.base.chunks[ci].id} | pos={ctx.base.chunks[ci].start/total:.0%} | frozen_G7={'yes' if ci in frozen_ids else 'no'}]\n" + "\n".join(facts))
    return "\n\n".join(result)

def rerank(client, question, card_text, valid_ids, limit=8):
    options="\n".join(f"{LETTERS[i]}. {v}" for i,v in enumerate(question["choices"][:4]))
    prompt=f"QUESTION\n{question['question']}\n\nOPTIONS\n{options}\n\nGRAPH CANDIDATE CARDS\n{card_text}\n\nSelect exactly {limit} source chunk IDs most likely to contain decisive evidence needed to distinguish the four options. Prefer explicit final revelations, confessions, causal explanations, identity corrections, and facts marked decisive/central. Demote decoys, early suspicion, generic mentions and repeated cards. Do not answer the question. Return JSON only: {{\"selected_chunk_ids\":[\"c_1\"]}}"
    raw=client.complete("You are a graph evidence scheduler. Use graph metadata only and never answer the multiple-choice question.",prompt,max_tokens=260)
    ids,data=parse_ids(raw); ids=native.unique([v for v in ids if v in valid_ids])
    return ids[:limit],data,raw

def compact_dossier(ctx, frozen, selected_ids):
    by_id={c.id:(i,c) for i,c in enumerate(ctx.base.chunks)}; chunks=[]
    for rank,cid in enumerate(selected_ids):
        if cid not in by_id: continue
        i,c=by_id[cid]; chunks.append({"id":cid,"index":i,"start":c.start,"end":c.end,"text":c.text,"for_options":["A","B","C","D"],"best_rank":rank,"best_score":1/(rank+1)})
    chunks.sort(key=lambda row:row["start"]); chosen={r["id"] for r in chunks}
    links=[r for r in frozen["links"] if r["chunk_id"] in chosen]
    return {"chunks":chunks,"links":links,"diagnostics":{"selected_chunk_ids":[r["id"] for r in chunks],"source_chars":sum(len(r["text"]) for r in chunks),"gold_access":False,"baseline_access":False}}

def main():
    p=argparse.ArgumentParser(); p.add_argument("--novels",nargs="+",default=["93","97","99"]); p.add_argument("--graph-root",type=Path,required=True); p.add_argument("--out-root",type=Path,required=True); p.add_argument("--model",default="qwen3.5:9b"); p.add_argument("--num-ctx",type=int,default=16384); p.add_argument("--max-questions",type=int,default=0); args=p.parse_args()
    args.out_root.mkdir(parents=True,exist_ok=True); cases=merged_cases(args.novels); client=NativeOllamaNoThinkClient(args.model,num_ctx=args.num_ctx); done=correct=total=0
    signature=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
    for novel in args.novels:
        case=cases[novel]; graph=json.loads((args.graph_root/"novels"/novel/"graph.json").read_text(encoding="utf-8")); ctx=C8Context.build(graph,case["text"],None); source_matrix=chunk_embeddings(novel,[c.text for c in ctx.base.chunks]); docs,maps=native.graph_docs(ctx); graph_matrix=native.cached(novel,docs)
        queries=[f"Question: {q['question']}\nCandidate answer: {choice}" for q in case["questions"] for choice in q["choices"][:4]]; qmatrix=embed(queries)
        for qi,q in enumerate(case["questions"][:args.max_questions or None]):
            total+=1; path=args.out_root/"answers"/novel/f"q{qi:02d}.json"
            if path.exists():
                old=json.loads(path.read_text(encoding="utf-8"))
                if old.get("signature")==signature: done+=1; correct+=bool(old["correct"]); continue
            vectors=qmatrix[qi*4:qi*4+4]; frozen=build_dossier(ctx,source_matrix,vectors,q,chunk_limit=6,link_limit=10); pool,by_chunk=graph_pool(ctx,docs,maps,graph_matrix,vectors,q,frozen); frozen_ids={int(r["index"]) for r in frozen["chunks"]}; valid={ctx.base.chunks[i].id for i in pool}; ids,rank_data,rank_raw=rerank(client,q,cards(ctx,docs,pool,by_chunk,frozen_ids),valid)
            fallback=[ctx.base.chunks[i].id for i in pool]; ids=native.unique(ids+fallback)[:8]; dossier=compact_dossier(ctx,frozen,ids); answer=complete_choice(client,"You are a conservative detective-novel evidence judge. Do not use outside knowledge.",answer_prompt(q,dossier)); letter=normalize_letter(answer.get("selected_letter")) or "?"; gold=LETTERS[int(q["gold_index"])]; row={"version":VERSION,"signature":signature,"novel":novel,"qi":qi,"qid":q["qid"],"question":q["question"],"choices":q["choices"][:4],"gold_letter":gold,"selected_letter":letter,"correct":letter==gold,"retrieval":dossier,"candidate_pool_ids":fallback,"reranker":rank_data,"reranker_raw":rank_raw,"answer":answer,"baseline_access":False,"gold_access":False}
            path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(row,ensure_ascii=False,indent=1),encoding="utf-8"); done+=1; correct+=row["correct"]; print(f"[{done}/{total}] {novel}/q{qi} G9={letter} gold={gold} correct={row['correct']}",flush=True)
    summary={"version":VERSION,"correct":correct,"total":done,"accuracy":correct/done if done else 0}; (args.out_root/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
