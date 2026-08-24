"""Run G7 QA with gold-blind graph-native semantic expansion to 28 chunks."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts")); sys.path.insert(0, str(ROOT / "src"))
import audit_dqa30_graph_native_recall as native
import run_dqa_g7_pure_graph as runner
from run_dqa30_g6_graph_expansion import answer_prompt, build_dossier as frozen_dossier
_cache = {}

def graph_native_dossier(ctx, source_matrix, option_vectors, question, *, chunk_limit=28, link_limit=10):
    base = frozen_dossier(ctx, source_matrix, option_vectors, question, chunk_limit=6, link_limit=link_limit)
    key = id(ctx)
    if key not in _cache:
        documents, maps = native.graph_docs(ctx)
        novel = str(question["qid"]).split("_")[1]
        _cache[key] = (native.cached(novel, documents), maps)
    graph_matrix, maps = _cache[key]
    graph_rank = native.rank_graph(graph_matrix, maps, option_vectors)
    frozen = [int(row["index"]) for row in base["chunks"]]
    selected = native.unique(frozen + graph_rank)[:chunk_limit]
    candidate_map = {int(row["index"]): sorted(set(row.get("for_options") or [])) for row in base["chunks"]}
    base["chunks"] = [
        {"id": ctx.base.chunks[index].id, "index": index, "start": ctx.base.chunks[index].start,
         "end": ctx.base.chunks[index].end, "text": ctx.base.chunks[index].text,
         "for_options": candidate_map.get(index, ["A", "B", "C", "D"]), "best_rank": rank,
         "best_score": float(1 / (rank + 1))}
        for rank, index in enumerate(sorted(selected, key=lambda value: ctx.base.chunks[value].start))
    ]
    selected_ids = {row["id"] for row in base["chunks"]}
    base["links"] = [row for row in base["links"] if row["chunk_id"] in selected_ids]
    base["diagnostics"].update({"selected_chunk_ids": [row["id"] for row in base["chunks"]],
                                "source_chars": sum(len(row["text"]) for row in base["chunks"]),
                                "retrieval_policy": "G7-6 plus graph-native entity/relation semantic index",
                                "gold_access": False})
    return base

_merged_cases = runner.merged_cases
def compatible_cases(novels):
    cases = _merged_cases(novels)
    for case in cases.values():
        for question in case["questions"]:
            question.setdefault("answer_letter", "ABCD"[int(question["gold_index"])])
    return cases

runner.build_dossier = graph_native_dossier
runner.prompt = answer_prompt
runner.merged_cases = compatible_cases
if __name__ == "__main__": runner.main()
