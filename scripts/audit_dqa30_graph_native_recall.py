"""Gold-blind graph-native retrieval audit for DetectiveQA."""
from __future__ import annotations
import argparse, hashlib, json, sys
from collections import defaultdict
from pathlib import Path
from typing import Any
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts")); sys.path.insert(0, str(ROOT / "src"))
from analyze_dense_retrieval import chunk_embeddings, embed
from audit_dqa30_graph_recall import annotation, g7, gold_chunk_ids, paragraph_spans
from build_c_next10_graphs import merged_cases
from c13_option_rebuttal import _option_packet
from c8_graph_passage import C8Context

NOVELS = ["93", "97", "99", "100", "103", "104", "105", "106", "107", "108"]
GRAPH_ROOT = ROOT / "outputs" / "four_datasets" / "dqa30_attention" / "batch03"
CACHE = ROOT / "outputs" / "four_datasets" / "dqa_bgem3_graph_index_cache"

def exact_ids(ctx, positions):
    markers = {f"[{p}]" for p in positions}
    return {i for i, c in enumerate(ctx.base.chunks) if any(m in c.text for m in markers)}

def graph_docs(ctx):
    docs, maps = [], []
    for node in ctx.base.store.nodes:
        chunks = sorted(ctx.node_to_chunks.get(node["id"], set()))
        if chunks:
            aliases = " ; ".join(map(str, node.get("aliases", [])[:8])); evidence = " ; ".join(map(str, node.get("evidence", [])[:8]))
            priority = "DECISIVE CENTRAL" if int(node.get("salience") or 3) >= 4 else ""
            docs.append(f"ENTITY {node.get('name','')}; TYPE {node.get('type','')}; ALIASES {aliases}; DESCRIPTION {node.get('description','')}; ATTRIBUTES {node.get('attributes',{})}; PRIORITY {priority}; GRAPH EVIDENCE {evidence}"); maps.append(chunks)
    for ei, edge in enumerate(ctx.base.store.edges):
        chunks = sorted(ctx.edge_to_chunks.get(ei, set()))
        if chunks:
            source = ctx.base.store.by_id.get(edge.get("source"), {}).get("name", ""); target = ctx.base.store.by_id.get(edge.get("target"), {}).get("name", "")
            priority = "DECISIVE CENTRAL" if int(edge.get("importance") or 3) >= 4 else ""
            docs.append(f"GRAPH FACT {source}; RELATION {edge.get('type','')}; {target}; PRIORITY {priority}; DECOY {bool(edge.get('decoy',False))}; GROUNDED EVIDENCE {edge.get('evidence','')}"); maps.append(chunks)
    return docs, maps

def cached(novel, docs):
    CACHE.mkdir(parents=True, exist_ok=True); digest = hashlib.sha256("\n<G>\n".join(docs).encode()).hexdigest(); path = CACHE / f"{novel}.npz"
    if path.exists():
        old = np.load(path, allow_pickle=False)
        if str(old["digest"].item()) == digest: return old["embeddings"]
    blocks=[]
    for start in range(0, len(docs), 32):
        blocks.append(embed(docs[start:start+32])); print(f"graph embed {novel}: {min(start+32,len(docs))}/{len(docs)}", flush=True)
    matrix=np.vstack(blocks); np.savez_compressed(path, embeddings=matrix, digest=np.asarray(digest)); return matrix

def rank_graph(matrix, maps, vectors):
    scores=np.max(matrix @ vectors.T, axis=1); by_chunk=defaultdict(list)
    for di, score in enumerate(scores):
        for ci in maps[di]: by_chunk[ci].append(float(score))
    fused={}
    for ci, values in by_chunk.items():
        values.sort(reverse=True); fused[ci]=values[0]+(0.15*values[1] if len(values)>1 else 0)
    return [ci for ci,_ in sorted(fused.items(), key=lambda x:x[1], reverse=True)]

def unique(values):
    seen=set(); return [v for v in values if not (v in seen or seen.add(v))]

def neighbors(values, total, limit):
    result=list(values)
    for i in values:
        for j in (i-1,i+1):
            if 0<=j<total and j not in result:
                result.append(j)
                if len(result)>=limit: return result
    return result

def summary(rows, key):
    return {"questions":len(rows), "strict_marker_recall":sum(bool(r[key]&r["strict"]) for r in rows)/len(rows), "content_overlap_recall":sum(bool(r[key]&r["overlap"]) for r in rows)/len(rows), "answer_marker_recall":sum(bool(r[key]&r["answer"]) for r in rows)/len(rows), "mean_chunks":sum(len(r[key]) for r in rows)/len(rows), "mean_chars":sum(sum(r["chars"][i] for i in r[key]) for r in rows)/len(rows)}

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--novels", nargs="+", default=NOVELS); parser.add_argument("--graph-root", type=Path, default=GRAPH_ROOT); parser.add_argument("--out", type=Path, default=ROOT/"reports"/"DQA30_GRAPH_NATIVE_RECALL_20260824.json"); args=parser.parse_args()
    cases=merged_cases(args.novels); rows=[]
    for novel in args.novels:
        case=cases[novel]; graph=json.loads((args.graph_root/"novels"/novel/"graph.json").read_text(encoding="utf-8")); ctx=C8Context.build(graph, case["text"], None)
        source_matrix=chunk_embeddings(novel,[c.text for c in ctx.base.chunks]); docs,maps=graph_docs(ctx); graph_matrix=cached(novel,docs)
        query_texts=[f"Question: {q['question']}\nCandidate answer: {choice}" for q in case["questions"] for choice in q["choices"][:4]]; query_matrix=embed(query_texts)
        resolution_texts=[f"Final revelation, true solution, causal conclusion, confession, or decisive evidence answering: {q['question']} Options: {' ; '.join(q['choices'][:4])}" for q in case["questions"]]; resolution_matrix=embed(resolution_texts); spans=paragraph_spans(case["text"])
        for qi,q in enumerate(case["questions"]):
            vectors=query_matrix[qi*4:qi*4+4]; packets=[_option_packet(ctx,source_matrix,vectors[i],q,i) for i in range(4)]; candidates=[(float(c.get("rrf_score") or 0),oi,rank,int(c["index"])) for oi,p in enumerate(packets) for rank,c in enumerate(p["chunks"])]; frozen=g7(candidates,6); graph_rank=rank_graph(graph_matrix,maps,np.vstack([vectors,resolution_matrix[qi:qi+1]]))
            anno=annotation(novel,q["qid"]); clues={int(v) for v in anno.get("clue_position") or [] if int(v)>=0}; answer=int(anno.get("answer_position") or -1); positions=clues|({answer} if answer>=0 else set())
            variants={"frozen_g7_6":frozen,"graph_semantic_8":graph_rank[:8],"graph_semantic_12":graph_rank[:12],"graph_semantic_28":graph_rank[:28],"g7_plus_graph_12":unique(frozen+graph_rank)[:12],"g7_plus_graph_28":unique(frozen+graph_rank)[:28],"g7_plus_graph_neighbor16":neighbors(unique(frozen+graph_rank)[:12],len(ctx.base.chunks),16)}
            rows.append({"novel":novel,"qi":qi,"strict":exact_ids(ctx,positions),"overlap":gold_chunk_ids(ctx,positions,spans),"answer":exact_ids(ctx,{answer}) if answer>=0 else set(),"chars":[len(c.text) for c in ctx.base.chunks],**{k:set(v) for k,v in variants.items()}})
        print(f"audited graph-native retrieval for {novel}",flush=True)
    keys=[k for k in rows[0] if k.startswith(("frozen_","graph_","g7_"))]; report={"guard":"Gold is evaluation-only and never reaches retrieval.","variants":{k:summary(rows,k) for k in keys},"per_novel":{n:{k:summary([r for r in rows if r["novel"]==n],k) for k in keys} for n in args.novels}}
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(report["variants"],ensure_ascii=False,indent=2))
if __name__=="__main__": main()
