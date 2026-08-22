"""Convert MuSR (official JSON preferred, CSV fallback) into unified case JSONL.

MuSR official JSON adds `intermediate_trees` / `intermediate_data` (per-suspect
reasoning trees).  We keep them in question meta so downstream analysis can
compute "explicit-fact coverage" (semantic gold hit), since MuSR has no
evidence-paragraph / clue_position like DetectiveQA.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT.parent / "datasets" / "external" / "musr"
OUT_DIR = ROOT / "outputs" / "four_datasets" / "cases"

DOMAINS = ["murder_mystery", "object_placements", "team_allocation"]


def _leaf_facts(root) -> list[dict]:
    """Flatten a reasoning-tree root into leaf facts (value, fact_type, operator, prunable)."""
    out: list[dict] = []

    def walk(node, path):
        kids = node.get("children") or []
        if not kids:
            out.append(
                {
                    "text": (node.get("value") or "").strip(),
                    "fact_type": node.get("fact_type"),
                    "operator": node.get("operator"),
                    "deduction_type": node.get("deduction_type"),
                    "prunable": node.get("prunable"),
                    "path": path,
                }
            )
        for i, c in enumerate(kids):
            walk(c, path + [i])

    walk(root, [])
    return out


def load_official(domain: str) -> list[dict]:
    path = DATA_ROOT / "official" / f"{domain}.json"
    if not path.exists() or path.stat().st_size < 1000:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    cases: list[dict] = []
    for i, item in enumerate(data):
        ctx = str(item.get("context") or "")
        questions: list[dict] = []
        for qi, q in enumerate(item.get("questions") or []):
            choices = list(q.get("choices") or [])
            answer = q.get("answer")
            gold_ok = choices and isinstance(answer, int) and 0 <= answer < len(choices)
            trees = q.get("intermediate_trees") or []
            reasoning = []
            for tree in trees:
                for root in tree.get("root_structure") or []:
                    reasoning.append(
                        {
                            "root": (root.get("value") or "").strip(),
                            "leaves": _leaf_facts(root),
                        }
                    )
            questions.append(
                {
                    "qid": f"musr_{domain}_{i}_q{qi}",
                    "question": str(q.get("question") or ""),
                    "choices": choices or None,
                    "gold_index": answer if gold_ok else None,
                    "gold_text": choices[answer] if gold_ok else None,
                    "answer_format": "musr_num",
                    "meta": {
                        "domain": domain,
                        "reasoning_trees": reasoning,
                        "intermediate_data_len": len(q.get("intermediate_data") or []),
                    },
                }
            )
        cases.append(
            {
                "dataset": "musr",
                "case_id": f"musr_{domain}_{i}",
                "title": f"MuSR {domain} {i}",
                "text": ctx,
                "questions": questions,
                "meta": {"domain": domain},
            }
        )
    return cases


def load_csv(domain: str) -> list[dict]:
    import csv
    import ast

    path = DATA_ROOT / f"{domain}.csv"
    if not path.exists():
        return []
    cases: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for i, row in enumerate(csv.DictReader(fh)):
            try:
                choices = ast.literal_eval(row["choices"])
                if not isinstance(choices, list):
                    choices = [c.strip() for c in str(row["choices"]).split("|") if c.strip()]
                answer = int(row["answer_index"])
            except (ValueError, SyntaxError):
                choices = [row.get("answer_choice") or ""]
                answer = 0
            gold_ok = choices and 0 <= answer < len(choices)
            cases.append(
                {
                    "dataset": "musr",
                    "case_id": f"musr_{domain}_{i}",
                    "title": f"MuSR {domain} {i}",
                    "text": str(row.get("narrative") or row.get("story") or ""),
                    "questions": [
                        {
                            "qid": f"musr_{domain}_{i}_q0",
                            "question": str(row.get("question") or ""),
                            "choices": choices or None,
                            "gold_index": answer if gold_ok else None,
                            "gold_text": choices[answer] if gold_ok else None,
                            "answer_format": "musr_num",
                            "meta": {"domain": domain},
                        }
                    ],
                    "meta": {"domain": domain},
                }
            )
    return cases


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_cases: list[dict] = []
    for domain in DOMAINS:
        cases = load_official(domain) or load_csv(domain)
        print(f"{domain}: {len(cases)} cases (official={bool(load_official(domain))})")
        all_cases.extend(cases)
    out = OUT_DIR / "musr.jsonl"
    with open(out, "w", encoding="utf-8") as fh:
        for case in all_cases:
            fh.write(json.dumps(case, ensure_ascii=False) + "\n")
    print(f"wrote {len(all_cases)} cases -> {out}")


if __name__ == "__main__":
    main()
