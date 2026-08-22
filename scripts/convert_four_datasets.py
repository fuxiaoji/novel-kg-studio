"""Convert the four benchmark datasets into unified case JSONL for Novel KG Studio."""

from __future__ import annotations

import ast
import csv
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT.parent / "datasets" / "external"
OUT_DIR = ROOT / "outputs" / "four_datasets" / "cases"


@dataclass
class QItem:
    qid: str
    question: str
    choices: list[str] | None = None
    gold_index: int | None = None
    gold_text: str | None = None
    gold_pairs: list[list[int]] | None = None
    mask_char: int | None = None
    answer_format: str = "letter"
    meta: dict = field(default_factory=dict)


@dataclass
class Case:
    dataset: str
    case_id: str
    title: str
    text: str
    questions: list[QItem]
    skip_pass1: bool = False
    meta: dict = field(default_factory=dict)


def _dump(cases: list[Case], name: str) -> None:
    out = OUT_DIR / f"{name}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for case in cases:
            fh.write(json.dumps(asdict(case), ensure_ascii=False) + "\n")
    print(f"{name}: {len(cases)} cases -> {out}")


def load_musr() -> list[Case]:
    cases: list[Case] = []
    for domain in ["murder_mystery", "object_placements", "team_allocation"]:
        path = DATA_ROOT / "musr" / f"{domain}.csv"
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        for i, row in enumerate(rows):
            try:
                choices = ast.literal_eval(row["choices"])
                if not isinstance(choices, list):
                    choices = [c.strip() for c in str(row["choices"]).split("|") if c.strip()]
                answer_index = int(row["answer_index"])
            except (ValueError, SyntaxError):
                choices = [str(row.get("answer_choice") or "")] if row.get("answer_choice") else []
                answer_index = 0
            gold_text = choices[answer_index] if choices and 0 <= answer_index < len(choices) else None
            cases.append(
                Case(
                    dataset="musr",
                    case_id=f"musr_{domain}_{i}",
                    title=row.get("title") or f"MuSR {domain} {i}",
                    text=str(row.get("narrative") or row.get("story") or ""),
                    questions=[
                        QItem(
                            qid=f"musr_{domain}_{i}_q0",
                            question=str(row.get("question") or ""),
                            choices=choices or None,
                            gold_index=answer_index if choices else None,
                            gold_text=gold_text,
                        )
                    ],
                    meta={"domain": domain},
                )
            )
    return cases


def load_detectbench() -> list[Case]:
    cases: list[Case] = []
    for split in ["dev", "test", "test-hard", "test-distract"]:
        path = DATA_ROOT / "detectbench" / f"{split}.jsonl"
        decoder = json.JSONDecoder()
        text = path.read_text(encoding="utf-8")
        rows = []
        pos = 0
        while pos < len(text):
            while pos < len(text) and text[pos] in " \t\r\n":
                pos += 1
            if pos >= len(text):
                break
            obj, end = decoder.raw_decode(text, pos)
            rows.append(obj)
            pos = end
        for i, row in enumerate(rows):
            choices = [str(c) for c in (row.get("options") or [])]
            raw_answer = row.get("answer")
            answer = int(raw_answer) if raw_answer not in (None, "") else -1
            valid = choices and 0 <= answer < len(choices)
            gold_text = choices[answer] if valid else None
            cases.append(
                Case(
                    dataset="detectbench",
                    case_id=f"detectbench_{split}_{row.get('id', i)}",
                    title=f"DetectBench {split} {row.get('id', i)}",
                    text=str(row.get("context") or ""),
                    questions=[
                        QItem(
                            qid=f"detectbench_{split}_{row.get('id', i)}_q0",
                            question=str(row.get("question") or ""),
                            choices=choices or None,
                            gold_index=answer if valid else None,
                            gold_text=gold_text,
                            meta={"dataset": "detectbench", "split": split},
                        )
                    ],
                    meta={"split": split, "has_clue_graph": bool(row.get("clue_graph"))},
                )
            )
    return cases


def load_detectiveqa() -> list[Case]:
    base = DATA_ROOT / "detectiveqa"
    novels: dict[int, str] = {}
    for path in base.glob("novel_data_en/*.txt"):
        m = re.match(r"(\d+)", path.stem)
        if m:
            novels[int(m.group(1))] = path.read_text(encoding="utf-8")
    cases: list[Case] = []
    for anno_dir in sorted(base.glob("anno_data_en/*")):
        source = anno_dir.name
        for path in sorted(anno_dir.glob("*.json")):
            novel_id = int(path.stem)
            text = novels.get(novel_id, "")
            if not text:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            anno = data[0] if isinstance(data, list) else data
            questions: list[QItem] = []
            for qi, q in enumerate(anno.get("questions") or []):
                options = q.get("options") or {}
                letters = sorted(options.keys())
                choices = [str(options[k]).strip() for k in letters]
                answer_letter = str(q.get("answer") or "").strip().upper()
                gold_index = letters.index(answer_letter) if answer_letter in letters else None
                mask_char = None
                try:
                    para = int(q.get("answer_position") or -1)
                    if para >= 0:
                        marker = f"\n[{para}] "
                        idx = text.find(marker)
                        if idx < 0 and text.startswith(f"[{para}] "):
                            idx = 0
                        mask_char = idx if idx >= 0 else None
                except (TypeError, ValueError):
                    pass
                questions.append(
                    QItem(
                        qid=f"detectiveqa_{novel_id}_{source}_{qi}",
                        question=str(q.get("question") or "").strip(),
                        choices=choices or None,
                        gold_index=gold_index,
                        gold_text=choices[gold_index] if gold_index is not None else None,
                        mask_char=mask_char,
                    )
                )
            if questions:
                cases.append(
                    Case(
                        dataset="detectiveqa",
                        case_id=f"detectiveqa_{novel_id}_{source}",
                        title=f"DetectiveQA {novel_id} ({source})",
                        text=text,
                        questions=questions,
                        meta={"novel_id": novel_id, "anno_source": source},
                    )
                )
    return cases


def load_turnabout() -> list[Case]:
    cases: list[Case] = []
    for name in ["AA_integrated_dataset.json", "DR_integrate_dataset.json"]:
        path = DATA_ROOT / "turnabout_llm" / "data" / name
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        tag = "AA" if name.startswith("AA") else "DR"
        for i, ep in enumerate(data):
            inp = ep.get("input") or {}
            full_input = str(inp.get("full_input") or "")
            if not full_input:
                full_input = "\n".join(
                    str(inp.get(k) or "") for k in ["prefix", "evidence", "testimonies", "summarized_context", "suffix"]
                )
            question = str(inp.get("suffix") or "Which evidence and testimony contradict each other?")
            output = ep.get("output")
            gold_pairs = []
            if isinstance(output, list):
                for pair in output:
                    if isinstance(pair, list) and len(pair) == 2:
                        # integrated dataset stores [testimony_index, evidence_index]
                        gold_pairs.append([int(pair[1]), int(pair[0])])
            cases.append(
                Case(
                    dataset="turnabout",
                    case_id=f"turnabout_{tag}_{i}",
                    title=str(ep.get("source") or f"Turnabout {tag} {i}"),
                    text=full_input,
                    questions=[
                        QItem(
                            qid=f"turnabout_{tag}_{i}_q0",
                            question=question,
                            choices=None,
                            gold_pairs=gold_pairs,
                            answer_format="pair",
                        )
                    ],
                    skip_pass1=True,
                    meta={"episode": str(ep.get("source") or "")},
                )
            )
    return cases


def main() -> None:
    loaders = {"musr": load_musr, "detectbench": load_detectbench, "detectiveqa": load_detectiveqa, "turnabout": load_turnabout}
    for name, loader in loaders.items():
        _dump(loader(), name)


if __name__ == "__main__":
    main()
