"""Re-judge unparsed MuSR-local answers with an external DeepSeek judge (same protocol as DetectQA)."""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from eval_four_datasets import UrllibClient  # noqa: E402
from novel_kg_studio.cache import load_json, save_json  # noqa: E402

OUT = ROOT / "outputs" / "four_datasets" / "musr_local"
RESULTS = OUT / "results.json"
JUDGE_DIR = OUT / "rejudge"
METHODS = ["basic", "v4", "v5.1", "v5.2", "v7", "vote"]

JUDGE_PROMPT = """Question: {question}
Choices (1-based):
{choices}
Gold answer (index): {gold_index} -> {gold_text}
Model answer: {answer}
Decide whether the model answer selects the gold option (it may mention the option text or give a prose conclusion).
Return strict JSON only: {{"correct": true/false, "note": "one short reason"}}"""


def judge_one(client, qid: str, method: str, row: dict) -> tuple[bool, str]:
    key = f"{qid}|{method}"
    path = JUDGE_DIR / f"{key.replace('/', '_').replace('|', '_')}.json"
    cached = load_json(path)
    if cached:
        return bool(cached["correct"]), str(cached["note"])
    answer = row[method]["answer"]
    choices = "\n".join(f"{i+1}. {c}" for i, c in enumerate(row.get("choices", [])))
    prompt = (
        "Question: " + row["question"] + "\n"
        "Choices (1-based):\n" + choices + "\n"
        "Gold answer (index): " + str(row.get("gold_index")) + " -> " + str(row.get("gold_text", "")) + "\n"
        "Model answer: " + answer[:2500] + "\n"
        "Decide whether the model answer selects the gold option (it may mention the option text or give a prose conclusion).\n"
        'Return strict JSON only: {"correct": true/false, "note": "one short reason"}'
    )
    payload = client.complete_json(
        "You are a strict but fair answer judge for MuSR.",
        prompt,
    )
    verdict = {
        "correct": bool(payload.get("correct")) if isinstance(payload, dict) else False,
        "note": str(payload.get("note") or "") if isinstance(payload, dict) else "",
    }
    save_json(path, verdict)
    return verdict["correct"], verdict["note"]


def main() -> None:
    client = UrllibClient("deepseek-v4-flash", reasoning_effort="low")
    choices_by_qid = {}
    cases_path = ROOT / "outputs" / "four_datasets" / "cases" / "musr.jsonl"
    for line in cases_path.read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        for q in case["questions"]:
            choices_by_qid[q["qid"]] = q.get("choices") or []
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    rows = data["results"]
    tasks = []
    for r in rows:
        r["choices"] = choices_by_qid.get(r["qid"], [])
        for m in METHODS:
            if m in r and not r[m]["note"].startswith("parsed"):
                tasks.append((r, m))
    print(f"unparsed to re-judge: {len(tasks)}", flush=True)
    JUDGE_DIR.mkdir(parents=True, exist_ok=True)
    done = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(judge_one, client, r["qid"], m, r): (r, m) for r, m in tasks}
        for fut in as_completed(futs):
            r, m = futs[fut]
            try:
                correct, note = fut.result()
                r[m]["correct"] = correct
                r[m]["note"] = note
            except Exception as exc:
                r[m]["note"] = f"rejudge failed: {type(exc).__name__}: {exc}"
            done += 1
            if done % 25 == 0:
                print(f"[rejudge] {done}/{len(tasks)}", flush=True)
                RESULTS.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    RESULTS.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    # summary
    by_domain: dict[str, dict[str, list[bool]]] = {}
    for r in rows:
        d = by_domain.setdefault(r["domain"], {})
        for m in METHODS:
            if m in r:
                d.setdefault(m, []).append(bool(r[m]["correct"]))
    print("\n=== REJUDGED RESULTS ===", flush=True)
    for domain, mdict in by_domain.items():
        line = " | ".join(f"{m}: {sum(v)}/{len(v)} ({sum(v)/len(v):.0%})" for m, v in mdict.items())
        print(f"{domain}: {line}", flush=True)


if __name__ == "__main__":
    main()
