"""Gold-paragraph retrieval audit for matched C8 BM25 and graph passage sets."""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from build_c_next10_graphs import merged_cases  # noqa: E402
from c8_graph_passage import C8Context, retrieve_bm25, retrieve_graph  # noqa: E402
from run_c8_20 import NOVELS, graph_path  # noqa: E402

DATA = ROOT.parent / "datasets" / "external" / "detectiveqa"
BASE = ROOT / "outputs" / "four_datasets"
QID_RE = re.compile(r"^detectiveqa_(\d+)_(AIsup_anno|human_anno)_(\d+)$")
PARA_RE = re.compile(r"(?m)^\[(\d+)\]\s*")


def load_closed(root: Path) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for path in root.glob("*/*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        result[row["qid"]] = bool(row["correct"])
    return result


def annotations(novel: str) -> dict[tuple[str, int], dict[str, Any]]:
    result = {}
    for source in ("AIsup_anno", "human_anno"):
        path = DATA / "anno_data_en" / source / f"{novel}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        record = data[0] if isinstance(data, list) else data
        for index, row in enumerate(record.get("questions", [])):
            result[(source, index)] = row
    return result


def paragraph_spans(text: str) -> dict[int, tuple[int, int]]:
    matches = list(PARA_RE.finditer(text))
    return {
        int(match.group(1)): (match.start(), matches[index + 1].start() if index + 1 < len(matches) else len(text))
        for index, match in enumerate(matches)
    }


def overlaps(chunks: list[dict[str, Any]], span: tuple[int, int] | None) -> bool:
    if span is None:
        return False
    start, end = span
    return any(int(chunk["start"]) < end and int(chunk["end"]) > start for chunk in chunks)


def summarize(rows: list[dict[str, Any]], method: str) -> dict[str, Any]:
    clue_total = sum(row["clue_total"] for row in rows)
    clue_hits = sum(row[f"{method}_clue_hits"] for row in rows)
    return {
        "questions": len(rows),
        "any_clue_hit": sum(row[f"{method}_any_clue"] for row in rows) / len(rows) if rows else 0.0,
        "clue_paragraph_recall": clue_hits / clue_total if clue_total else 0.0,
        "answer_paragraph_hit": sum(row[f"{method}_answer_hit"] for row in rows) / len(rows) if rows else 0.0,
        "average_chars": sum(row[f"{method}_chars"] for row in rows) / len(rows) if rows else 0.0,
    }


def main() -> None:
    cases = merged_cases(NOVELS)
    qwen = load_closed(BASE / "dqa_qwen_question_only20" / "answers")
    deepseek = load_closed(BASE / "dqa_deepseek_v4flash_full_vs_question20_nothinking" / "answers" / "question_only")
    rows = []
    for novel in NOVELS:
        case = cases[novel]
        graph = json.loads(graph_path(novel).read_text(encoding="utf-8"))
        ctx = C8Context.build(graph, case["text"], None)
        spans = paragraph_spans(case["text"])
        anno = annotations(novel)
        for qi, q in enumerate(case["questions"]):
            match = QID_RE.match(q["qid"])
            if not match:
                raise ValueError(q["qid"])
            raw = anno[(match.group(2), int(match.group(3)))]
            clues = sorted({int(pos) for pos in raw.get("clue_position", []) if int(pos) >= 0 and int(pos) in spans})
            answer_pos = int(raw.get("answer_position", -1))
            packages = {"bm25": retrieve_bm25(ctx, q), "graph": retrieve_graph(ctx, q)}
            row: dict[str, Any] = {
                "novel": novel,
                "qi": qi,
                "qid": q["qid"],
                "clue_total": len(clues),
                "qwen_closed": qwen[q["qid"]],
                "deepseek_closed": deepseek[q["qid"]],
            }
            for method, package in packages.items():
                chunks = package["chunks"]
                clue_hits = sum(overlaps(chunks, spans.get(pos)) for pos in clues)
                row[f"{method}_clue_hits"] = clue_hits
                row[f"{method}_any_clue"] = clue_hits > 0
                row[f"{method}_answer_hit"] = overlaps(chunks, spans.get(answer_pos))
                row[f"{method}_chars"] = sum(len(chunk["text"]) for chunk in chunks)
            rows.append(row)

    sets = {
        "all": rows,
        "qwen_hard": [row for row in rows if not row["qwen_closed"]],
        "conservative_hard": [row for row in rows if not row["qwen_closed"] and not row["deepseek_closed"]],
    }
    report: dict[str, Any] = {"sets": {}, "graph_increment": {}}
    for name, subset in sets.items():
        report["sets"][name] = {method: summarize(subset, method) for method in ("bm25", "graph")}
        report["graph_increment"][name] = {
            "adds_any_gold_clue": sum(row["graph_any_clue"] and not row["bm25_any_clue"] for row in subset),
            "loses_all_gold_clues": sum(row["bm25_any_clue"] and not row["graph_any_clue"] for row in subset),
            "adds_answer_paragraph": sum(row["graph_answer_hit"] and not row["bm25_answer_hit"] for row in subset),
            "loses_answer_paragraph": sum(row["bm25_answer_hit"] and not row["graph_answer_hit"] for row in subset),
        }
    out = BASE / "dqa_qwen_c8_20"
    out.mkdir(parents=True, exist_ok=True)
    (out / "retrieval_audit.json").write_text(json.dumps({"report": report, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
