"""Create one auditable JSONL record per question and method for the frozen study."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs" / "four_datasets"
OUT = ROOT / "paper" / "generated" / "dqa30_answer_records.jsonl"


def load(path: Path) -> dict[str, Any]: return json.loads(path.read_text(encoding="utf-8"))
def index(root: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    result = {}
    for path in root.rglob("q*.json"):
        item = load(path)
        if item.get("qid"): result[item["qid"]] = (path, item)
    return result
def majority(votes: list[str]) -> str:
    counts=Counter(votes); n=max(counts.values()); tied={x for x,c in counts.items() if c==n}
    return votes[0] if votes[0] in tied else sorted(tied)[0]
def char_count(answer: dict[str, Any], question: str, choices: list[str]) -> int:
    if answer.get("input_characters") is not None: return int(answer["input_characters"])
    retrieval=answer.get("retrieval") or {}; chunks=retrieval.get("chunks") or []
    links=retrieval.get("links") or retrieval.get("graph_links") or []
    return len(question)+sum(map(len,choices))+sum(len(str(c.get("text") or "")) for c in chunks)+len(json.dumps(links,ensure_ascii=False))
def normalized(method: str, answer: dict[str, Any], gold: str, question: str, choices: list[str], source: Path, elapsed: float | None=None) -> dict[str, Any]:
    selected=answer.get("selected_letter"); valid=isinstance(selected,str) and selected in "ABCD"; chars=char_count(answer,question,choices)
    return {"method":method,"selected_letter":selected,"correct":bool(valid and selected==gold),"parse_status":"valid_option" if valid else "invalid_or_missing","retrieval_evidence":answer.get("retrieval") or {"status":"not_applicable_or_not_archived"},"input_characters":chars,"estimated_input_tokens":round(chars/4),"token_accounting":"character-based estimate unless exact count exists in source","elapsed_seconds":elapsed,"elapsed_accounting":"measured" if elapsed is not None else "not archived in legacy source","run_signature":answer.get("signature") or answer.get("source_hash") or answer.get("version") or "legacy-source-path","source_file":str(source.relative_to(ROOT))}


def main() -> None:
    old_rows=list(csv.DictReader((BASE/"dqa_local_c24_pure9_consensus20"/"per_question.csv").open(encoding="utf-8-sig",newline="")))
    g1=index(BASE/"dqa_qwen35_c15_20"/"answers"/"graph"); g2=index(BASE/"dqa_local_c21_20"/"answers"); g3=index(BASE/"dqa_local_c23_cyclic20"/"answers")
    g5=index(BASE/"dqa30_attention"/"g7_pure_graph_tight"/"answers"); tail=index(BASE/"dqa_qwen35_c15_20"/"answers"/"tail"); q0=index(BASE/"dqa_qwen35_c15_20"/"answers"/"question_only")
    records=[]
    for row in old_rows:
        qid=row["qid"]; novel=row["novel"]; qi=int(row["qi"]); gold=row["gold"]
        bpath=BASE/"dqa30_frozen_old20_baselines9b"/"answers"/novel/f"q{qi:02d}.json"; base=load(bpath)
        question=base["question"]; choices=base["choices"]; methods={}
        for method,source_index in (("G1",g1),("G2",g2),("G3",g3),("G5",g5),("B1",tail),("Q0",q0)):
            path,item=source_index[qid]; methods[method]=normalized(method,item,gold,question,choices,path)
        vote=[methods[m]["selected_letter"] for m in ("G1","G2","G3")]; selected=majority(vote)
        methods["G4"]={"method":"G4","selected_letter":selected,"correct":selected==gold,"parse_status":"valid_option","retrieval_evidence":{"derived_from":["G1","G2","G3"],"rule":"deterministic graph-only majority","votes":vote},"input_characters":max(methods[m]["input_characters"] for m in ("G1","G2","G3")),"estimated_input_tokens":max(methods[m]["estimated_input_tokens"] for m in ("G1","G2","G3")),"token_accounting":"derived composite; no extra model call","elapsed_seconds":None,"elapsed_accounting":"derived composite; no extra model call","run_signature":"graph-only-majority-v1","source_file":"derived"}
        elapsed=base.get("elapsed_seconds_new_calls")
        for method in ("B2","B3"): methods[method]=normalized(method,base["answers"][method],gold,question,choices,bpath,elapsed)
        records.append({"cohort":"old20","novel":novel,"qi":qi,"qid":qid,"question":question,"choices":choices,"gold_letter":gold,"graph_sha256":base["graph_sha256"],"methods":methods})
    new_root=BASE/"dqa30_attention"/"batch03_eval"/"answers"
    for path in sorted(new_root.glob("*/q*.json"),key=lambda p:(int(p.parent.name),p.name)):
        item=load(path); qid=item["qid"]; gold=item["gold_letter"]; question=item["question"]; choices=item["choices"]; methods={}
        for method in ("G1","G2","G3","B1","B2","B3","Q0"): methods[method]=normalized(method,item["answers"][method],gold,question,choices,path,item.get("elapsed_seconds"))
        vote=[methods[m]["selected_letter"] for m in ("G1","G2","G3")]; selected=majority(vote)
        methods["G4"]={"method":"G4","selected_letter":selected,"correct":selected==gold,"parse_status":"valid_option","retrieval_evidence":{"derived_from":["G1","G2","G3"],"rule":"deterministic graph-only majority","votes":vote},"input_characters":max(methods[m]["input_characters"] for m in ("G1","G2","G3")),"estimated_input_tokens":max(methods[m]["estimated_input_tokens"] for m in ("G1","G2","G3")),"token_accounting":"derived composite; no extra model call","elapsed_seconds":None,"elapsed_accounting":"derived composite; no extra model call","run_signature":"graph-only-majority-v1","source_file":"derived"}
        g5path,g5item=g5[qid]; methods["G5"]=normalized("G5",g5item,gold,question,choices,g5path)
        records.append({"cohort":"new10","novel":str(item["novel"]),"qi":int(item["qi"]),"qid":qid,"question":question,"choices":choices,"gold_letter":gold,"graph_sha256":item["graph_sha256"],"methods":methods})
    if len(records)!=234 or any(set(record["methods"])!={"G1","G2","G3","G4","G5","B1","B2","B3","Q0"} for record in records): raise RuntimeError("incomplete answer archive")
    OUT.write_text("".join(json.dumps(record,ensure_ascii=False)+"\n" for record in records),encoding="utf-8")
    print(json.dumps({"questions":len(records),"method_answers":len(records)*9,"invalid":sum(method["parse_status"]!="valid_option" for record in records for method in record["methods"].values())},indent=2))

if __name__=="__main__": main()
